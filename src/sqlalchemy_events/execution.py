from __future__ import annotations

import asyncio
import importlib
import inspect
import multiprocessing as mp
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol

from .config import EventsConfig
from .defaults import (
    HANDLER_RETRY_ATTEMPTS,
    HANDLER_RETRY_BACKOFF,
    HANDLER_RETRY_BACKOFF_MAX,
    HANDLER_TIMEOUT,
    PROCESS_POOL_WORKERS,
    default_worker_count,
)
from .exceptions import EventHandlerConfigurationError
from .metrics import Metrics
from .types import CallPlan, Event, Handler, SyncExecutorKind


class LoggerLike(Protocol):
    def info(self, message: str) -> None: ...

    def warning(self, message: str) -> None: ...

    def error(self, message: str, exc_info: bool = ...) -> None: ...

    def debug(self, message: str) -> None: ...


@dataclass(frozen=True)
class HandlerFailure:
    handler: Handler
    error: BaseException
    attempts: int

    def describe(self) -> str:
        return (
            f'{self.handler.identity} for {self.handler.table}.{self.handler.event.value.lower()}'
        )


def _resolve_target(handler: Handler) -> tuple[type | None, Any]:
    func = handler.func
    qualname = getattr(func, '__qualname__', '')

    if '.' not in qualname or '<locals>' in qualname:
        return None, func

    module_name = getattr(func, '__module__', '')
    module = sys.modules.get(module_name)

    if module is None:
        try:
            module = importlib.import_module(module_name)
        except ImportError as ex:
            raise EventHandlerConfigurationError(
                f'Cannot import module {module_name!r} for handler {handler.identity!r}'
            ) from ex

    owner: Any = module
    parts = qualname.split('.')

    for part in parts[:-1]:
        owner = getattr(owner, part, None)
        if owner is None:
            raise EventHandlerConfigurationError(
                f'Cannot resolve class for handler {handler.identity!r}: {part!r} was not found'
            )

    if not isinstance(owner, type):
        raise EventHandlerConfigurationError(
            f'Handler {handler.identity!r} must be a module-level function '
            'or a classmethod/staticmethod of a class'
        )

    descriptor = inspect.getattr_static(owner, parts[-1], None)

    if isinstance(descriptor, classmethod):
        return owner, descriptor.__func__

    if isinstance(descriptor, staticmethod):
        return None, descriptor.__func__

    try:
        parameters = list(inspect.signature(func).parameters.values())
    except (TypeError, ValueError):
        parameters = []

    first = parameters[0] if parameters else None

    if first is not None and first.name == 'self':
        raise EventHandlerConfigurationError(
            f'Handler {handler.identity!r} is an instance method. '
            'Use @staticmethod, @classmethod, or a module-level function'
        )

    if first is not None and first.name == 'cls':
        raise EventHandlerConfigurationError(
            f'Handler {handler.identity!r} looks like a classmethod but is '
            'not decorated with @classmethod'
        )

    return None, func


def build_call_plan(handler: Handler) -> CallPlan:
    owner, target = _resolve_target(handler)

    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError) as ex:
        raise EventHandlerConfigurationError(
            f'Cannot inspect handler {handler.identity!r}: {ex}'
        ) from ex

    parameters = list(signature.parameters.values())
    prefix = 1 if owner is not None else 0

    for index, parameter in enumerate(parameters):
        if index < prefix:
            continue
        if parameter.name == 'rows':
            continue
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if parameter.default is inspect.Parameter.empty:
            raise EventHandlerConfigurationError(
                f'Handler {handler.identity!r} has required parameter '
                f"{parameter.name!r}; only the optional 'rows' parameter "
                'is supported'
            )

    rows_mode: str = 'none'
    rows_position: int | None = None
    rows_index = next(
        (index for index, parameter in enumerate(parameters) if parameter.name == 'rows'),
        None,
    )

    if rows_index is not None:
        rows_parameter = parameters[rows_index]

        if rows_parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            if rows_index != prefix:
                raise EventHandlerConfigurationError(
                    f'Handler {handler.identity!r} declares a positional-only '
                    "'rows' parameter at an unsupported position"
                )
            rows_mode = 'positional'
            rows_position = rows_index
        else:
            rows_mode = 'keyword'

    return CallPlan(
        handler=handler,
        func=target,
        is_async=inspect.iscoroutinefunction(target),
        rows_mode=rows_mode,  # type: ignore[arg-type]
        rows_position=rows_position,
        owner=owner,
    )


class HandlerExecutor:
    """Executes handlers with timeouts, retries and bounded concurrency."""

    def __init__(
        self,
        config: EventsConfig,
        logger: LoggerLike | None,
        metrics: Metrics,
    ) -> None:
        self.config = config
        self.logger = logger
        self.metrics = metrics

        self._thread_pool: ThreadPoolExecutor | None = None
        self._process_pool: ProcessPoolExecutor | None = None
        self._call_plans: dict[Handler, CallPlan] = {}

    def plan(self, handler: Handler) -> CallPlan:
        plan = self._call_plans.get(handler)
        if plan is None:
            plan = build_call_plan(handler)
            self._call_plans[handler] = plan
        return plan

    def register(self, handlers: tuple[Handler, ...]) -> None:
        for handler in handlers:
            self.plan(handler)

    async def execute(
        self,
        event: Event,
        handlers: tuple[Handler, ...],
    ) -> list[HandlerFailure]:
        if not handlers:
            return []

        plans = [self.plan(handler) for handler in handlers]

        if event.truncated:
            self.metrics.events_truncated += 1
            if self.logger is not None:
                self.logger.error(
                    '[SQLAlchemyEvents] Notification payload was truncated by '
                    f'PostgreSQL for {event.schema}.{event.table} '
                    f"({event.count} rows). Handlers receive empty 'rows'."
                )

        results = await asyncio.gather(
            *[self._execute_one(plan, event) for plan in plans],
            return_exceptions=False,
        )

        return [result for result in results if result is not None]

    async def _execute_one(
        self,
        plan: CallPlan,
        event: Event,
    ) -> HandlerFailure | None:
        handler = plan.handler
        rows = list(event.rows or ())
        attempts = HANDLER_RETRY_ATTEMPTS + 1
        last_error: BaseException | None = None

        for attempt in range(1, attempts + 1):
            self.metrics.handler_calls += 1

            try:
                await self._invoke(plan, rows)
                return None

            except asyncio.CancelledError:
                raise

            except Exception as ex:
                last_error = ex

                if attempt < attempts:
                    self.metrics.handler_retries += 1
                    delay = self._retry_delay(attempt)
                    if self.logger is not None:
                        self.logger.warning(
                            '[SQLAlchemyEvents] Handler '
                            f'{handler.identity!r} failed for '
                            f'{event.schema}.{event.table} '
                            f'({event.op.value.lower()}), retrying in '
                            f'{delay:.2f}s: {ex}'
                        )
                    if delay:
                        await asyncio.sleep(delay)

        self.metrics.handler_failures += 1
        self.metrics.record_error(last_error or 'unknown handler error')

        if self.logger is not None:
            self.logger.error(
                '[SQLAlchemyEvents] Handler '
                f'{handler.identity!r} failed for '
                f'{event.schema}.{event.table} '
                f'({event.op.value.lower()}) after '
                f'{attempts} attempt(s)',
                exc_info=True,
            )

        assert last_error is not None
        return HandlerFailure(
            handler=handler,
            error=last_error,
            attempts=attempts,
        )

    def _retry_delay(self, attempt: int) -> float:
        from .utils import backoff_delay

        return backoff_delay(
            attempt - 1,
            HANDLER_RETRY_BACKOFF,
            HANDLER_RETRY_BACKOFF_MAX,
        )

    def _call_args(
        self,
        plan: CallPlan,
        rows: list[Any],
    ) -> tuple[list[Any], dict[str, Any]]:
        args: list[Any] = [plan.owner] if plan.owner is not None else []

        if plan.rows_mode == 'none':
            return args, {}
        if plan.rows_mode == 'keyword':
            return args, {'rows': rows}

        args.insert(plan.rows_position or 0, rows)
        return args, {}

    async def _invoke(self, plan: CallPlan, rows: list[Any]) -> None:
        args, kwargs = self._call_args(plan, rows)
        timeout = HANDLER_TIMEOUT

        if plan.is_async:
            result = plan.func(*args, **kwargs)
            if inspect.isawaitable(result):
                await self._await_with_timeout(result, timeout)
            return

        loop = asyncio.get_running_loop()
        call = partial(plan.func, *args, **kwargs)

        if self.config.sync_executor is SyncExecutorKind.PROCESS:
            await self._run_in_process_pool(call)
            return

        future = loop.run_in_executor(
            self._get_thread_pool(),
            call,
        )

        await self._await_with_timeout(future, timeout)

    async def _await_with_timeout(
        self,
        awaitable: Any,
        timeout: float | None,
    ) -> None:
        if timeout is None:
            await awaitable
            return
        await asyncio.wait_for(awaitable, timeout=timeout)

    def _get_thread_pool(self) -> ThreadPoolExecutor:
        if self._thread_pool is None:
            self._thread_pool = ThreadPoolExecutor(
                max_workers=default_worker_count(),
                thread_name_prefix='sqlalchemy-events',
            )
        return self._thread_pool

    def _get_process_pool(self) -> ProcessPoolExecutor:
        if self._process_pool is None:
            self._process_pool = ProcessPoolExecutor(
                max_workers=PROCESS_POOL_WORKERS,
                mp_context=mp.get_context('spawn'),
            )
        return self._process_pool

    def _reset_process_pool(self) -> None:
        pool = self._process_pool
        self._process_pool = None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    async def _run_in_process_pool(self, call: Any) -> Any:
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._get_process_pool(), call)
        try:
            return await asyncio.wait_for(
                future,
                timeout=HANDLER_TIMEOUT,
            )
        except BrokenProcessPool:
            self._reset_process_pool()
            future = loop.run_in_executor(self._get_process_pool(), call)
            return await asyncio.wait_for(
                future,
                timeout=HANDLER_TIMEOUT,
            )

    def shutdown(self) -> None:
        thread_pool = self._thread_pool
        process_pool = self._process_pool
        self._thread_pool = None
        self._process_pool = None

        if thread_pool is not None:
            thread_pool.shutdown(wait=False, cancel_futures=True)

        if process_pool is not None:
            process_pool.shutdown(wait=False, cancel_futures=True)
