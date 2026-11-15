from __future__ import annotations

import inspect
import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase

from .config import EventsConfig
from .default_logger import DefaultLogger
from .defaults import STARTUP_TIMEOUT
from .discovery import autodiscover
from .dispatchers import BaseDispatcher
from .events import SaEventStrategy, sa_events_strategy
from .exceptions import (
    ConfigurationError,
    EventHandlerConfigurationError,
)
from .execution import build_call_plan
from .init_triggers_strategies.base import (
    DropResult,
    ManagedTrigger,
    SyncResult,
    TriggerPlan,
    TriggerTarget,
)
from .listener import PostgresListener
from .metrics import Health, Metrics
from .registry import get_event_handlers
from .types import Handler
from .utils import (
    dialect_resolver,
    model_table_name,
    resolve_id_columns,
)


class SQLAlchemyEvents:
    """Event-driven SQLAlchemy extension based on PostgreSQL NOTIFY."""

    CHANNEL = 'sqlalchemy_events'

    def __init__(
        self,
        engine: AsyncEngine | Engine,
        autodiscover_paths: Sequence[str | Path],
        schema: str = '',
        logger: logging.Logger | DefaultLogger | None = None,
        verbose: bool = True,
        *,
        config: EventsConfig | None = None,
        **overrides: Any,
    ) -> None:
        if config is not None and overrides:
            raise ConfigurationError('Pass either config or keyword overrides, not both')

        if config is None:
            config = EventsConfig()

        if overrides:
            try:
                config = replace(config, **overrides)
            except TypeError as ex:
                raise ConfigurationError(str(ex)) from ex

        if schema:
            config = replace(config, schema=schema)

        self.engine = engine
        self.autodiscover_paths = autodiscover_paths
        self.config = config
        self.logger = logger if logger is not None else (DefaultLogger() if verbose else None)
        self.verbose = verbose
        self.metrics = Metrics()

        self._started = False
        self._handlers_loaded = False
        self._handlers: tuple[Handler, ...] = ()
        self._strategy: SaEventStrategy | None = None
        self._resolved_schema = config.schema
        self._plans: list[TriggerPlan] = []
        self._dispatcher: BaseDispatcher | None = None
        self._listener: PostgresListener | None = None

    async def __call__(self) -> None:
        await self.start()

    async def __aenter__(self) -> SQLAlchemyEvents:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.stop()

    async def start(self) -> None:
        if self._started:
            return

        if not isinstance(self.engine, (AsyncEngine, Engine)):
            raise RuntimeError(
                "[SQLAlchemyEvents] 'engine' must be an instance of "
                'sqlalchemy.Engine or sqlalchemy.ext.asyncio.AsyncEngine.'
            )

        if not self.autodiscover_paths:
            if self.verbose and self.logger is not None:
                self.logger.warning(
                    '[SQLAlchemyEvents] No autodiscover paths specified. '
                    'Please provide at least one module path '
                    "(e.g., 'app.handlers') in 'autodiscover_paths' "
                    'during initialization.'
                )
            return

        if not isinstance(self.engine, AsyncEngine):
            raise RuntimeError(
                '[SQLAlchemyEvents] Sync Engine driver does not support '
                'async LISTEN/NOTIFY. Use AsyncEngine with asyncpg'
            )

        handlers = self._load_handlers()

        if not handlers:
            return

        strategy = self._get_strategy()
        self._resolved_schema = await self._resolve_schema()
        self._plans = self._build_plans(strategy, handlers)
        channels = {plan.channel for plan in self._plans}

        dispatcher = strategy.dispatcher_factory(
            engine=self._async_engine,
            registry=get_event_handlers(),
            config=self.config,
            default_schema=self._resolved_schema,
            logger=self.logger,
            metrics=self.metrics,
        )

        listener = PostgresListener(
            engine=self._async_engine,
            channels=channels,
            config=self.config,
            logger=self.logger,
            metrics=self.metrics,
            callback=dispatcher.on_notification,
        )

        try:
            await listener.start()
            await listener.wait_ready(timeout=STARTUP_TIMEOUT)

            if self.config.auto_sync:
                async with self._async_engine.begin() as conn:
                    result = await strategy.init_triggers.sync(
                        conn,
                        self._plans,
                        config=self.config,
                    )
                self._log_sync_result(result)

            await dispatcher.start()

        except BaseException:
            await listener.stop()
            await dispatcher.stop()
            raise

        self._dispatcher = dispatcher
        self._listener = listener
        self._started = True

    async def stop(self) -> None:
        if self._listener is not None:
            await self._listener.stop()
            self._listener = None

        if self._dispatcher is not None:
            await self._dispatcher.stop()
            self._dispatcher = None

        self._started = False

    @property
    def is_connected(self) -> bool:
        return bool(self._listener is not None and self._listener.connected)

    @property
    def is_healthy(self) -> bool:
        return self._started and self.is_connected

    async def health(self) -> Health:
        listener: dict[str, Any] = {}
        dispatcher: dict[str, Any] = {}

        if self._listener is not None:
            listener = await self._listener.health()

        if self._dispatcher is not None:
            dispatcher = await self._dispatcher.health()

        return Health(
            healthy=self.is_healthy,
            connected=self.is_connected,
            queue_size=int(dispatcher.get('queue_size', 0) or 0),
            last_notification_at=self.metrics.last_notification_at,
            details={
                'listener': listener,
                'dispatcher': dispatcher,
                'plan_count': len(self._plans),
                'schema': self._resolved_schema,
            },
        )

    def prometheus_metrics(self) -> str:
        return self.metrics.prometheus()

    def _load_handlers(self) -> tuple[Handler, ...]:
        if self._handlers_loaded:
            return self._handlers

        autodiscover(self.autodiscover_paths)

        handlers = get_event_handlers().all()

        if not handlers:
            if self.verbose and self.logger is not None:
                self.logger.info('[SQLAlchemyEvents] No handlers found')
            self._handlers = ()
            self._handlers_loaded = True
            return self._handlers

        self._validate_handlers(handlers)

        self._log_handler_summary(handlers)

        self._handlers = handlers
        self._handlers_loaded = True
        return handlers

    def _validate_handlers(self, handlers: tuple[Handler, ...]) -> None:
        for handler in handlers:
            model = handler.model

            if not isinstance(model, type) or not issubclass(
                model,
                DeclarativeBase,
            ):
                raise EventHandlerConfigurationError(
                    f'Handler {handler.identity!r} is registered for a '
                    'model that is not a DeclarativeBase'
                )

            model_table_name(model)
            resolve_id_columns(model)

            events = getattr(model, '__events__', None)

            if not events:
                raise EventHandlerConfigurationError(
                    f'Model {model.__name__} has a handler '
                    f'({handler.identity!r}) but is not decorated '
                    'with @with_events'
                )

            if handler.event not in events:
                raise EventHandlerConfigurationError(
                    f'Model {model.__name__} does not enable '
                    f'{handler.event.value} events required by handler '
                    f'{handler.identity!r}'
                )

            build_call_plan(handler)

    def _log_handler_summary(self, handlers: tuple[Handler, ...]) -> None:
        counts: defaultdict[str, int] = defaultdict(int)

        for handler in handlers:
            try:
                source = inspect.getsourcefile(handler.func)
            except (TypeError, OSError):
                source = None

            if source is None:
                source = getattr(handler.func, '__module__', 'unknown')

            counts[source] += 1

        if not (self.verbose and self.logger is not None):
            return

        for source, quantity in sorted(counts.items()):
            self.logger.info(
                f'[SQLAlchemyEvents] Registered {quantity} '
                f'{"handler" if quantity == 1 else "handlers"} '
                f'from {source!r}'
            )

    def _get_strategy(self) -> SaEventStrategy:
        if self._strategy is not None:
            return self._strategy

        dialect = dialect_resolver(self.engine)
        strategy = sa_events_strategy.get(dialect)

        if strategy is None:
            raise RuntimeError(
                f'[SQLAlchemyEvents] Unsupported database {dialect}. '
                f'This library supports only '
                f'{", ".join(sa_events_strategy.keys())}'
            )

        self._strategy = strategy
        return strategy

    async def _resolve_schema(self) -> str:
        if self.config.schema:
            return self.config.schema

        async with self._async_engine.connect() as conn:
            schema = await conn.scalar(text('SELECT current_schema()'))

        return str(schema or 'public')

    @property
    def _async_engine(self) -> AsyncEngine:
        return cast(AsyncEngine, self.engine)

    @staticmethod
    def _build_targets(handlers: tuple[Handler, ...]) -> list[TriggerTarget]:
        needs_rows: dict[tuple[type, Any], bool] = {}

        for handler in handlers:
            key = (handler.model, handler.event)
            plan = build_call_plan(handler)
            needs_rows[key] = needs_rows.get(key, False) or plan.needs_rows

        return [
            TriggerTarget(
                model=model,
                event=event,
                needs_rows=value,
            )
            for (model, event), value in sorted(
                needs_rows.items(),
                key=lambda item: (
                    item[0][0].__name__,
                    item[0][1].value,
                ),
            )
        ]

    def _build_plans(
        self,
        strategy: SaEventStrategy,
        handlers: tuple[Handler, ...],
    ) -> list[TriggerPlan]:
        targets = self._build_targets(handlers)

        return strategy.init_triggers.build_plans(
            targets,
            default_schema=self._resolved_schema,
            config=self.config,
        )

    async def sync_triggers(self, *, dry_run: bool = False) -> SyncResult:
        handlers = self._load_handlers()

        if not handlers:
            return SyncResult(dry_run=dry_run)

        strategy = self._get_strategy()
        self._resolved_schema = await self._resolve_schema()
        plans = self._build_plans(strategy, handlers)

        async with self._async_engine.begin() as conn:
            result = await strategy.init_triggers.sync(
                conn,
                plans,
                config=self.config,
                dry_run=dry_run,
            )

        if not dry_run:
            self._plans = plans

        self._log_sync_result(result)
        return result

    async def drop_triggers(
        self,
        *,
        all_managed: bool = False,
        dry_run: bool = False,
    ) -> DropResult:
        strategy = self._get_strategy()
        self._resolved_schema = await self._resolve_schema()

        plans: list[TriggerPlan] | None = None

        if not all_managed:
            handlers = self._load_handlers()
            if handlers:
                plans = self._build_plans(strategy, handlers)
            else:
                all_managed = True

        async with self._async_engine.begin() as conn:
            result = await strategy.init_triggers.drop(
                conn,
                config=self.config,
                default_schema=self._resolved_schema,
                plans=plans,
                all_managed=all_managed,
                dry_run=dry_run,
            )

        if self.verbose and self.logger is not None:
            self.logger.info(f'[SQLAlchemyEvents] {result.summary()}')

        return result

    async def list_triggers(self) -> list[ManagedTrigger]:
        strategy = self._get_strategy()
        self._resolved_schema = await self._resolve_schema()

        async with self._async_engine.connect() as conn:
            return await strategy.init_triggers.list_managed(
                conn,
                config=self.config,
                default_schema=self._resolved_schema,
            )

    def _log_sync_result(self, result: SyncResult) -> None:
        if not (self.verbose and self.logger is not None):
            return

        if result.changed:
            self.logger.info(f'[SQLAlchemyEvents] Triggers synchronized: {result.summary()}')

        for table in result.missing_tables:
            self.logger.warning(
                f'[SQLAlchemyEvents] Table {table!r} does not exist; triggers were not created'
            )
