import asyncio
import inspect
import logging

from collections import defaultdict
from pathlib import Path
from typing import Optional, Union

from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from .default_logger import DefaultLogger
from .discovery import autodiscover
from .events import SaEventStrategy, sa_events_strategy
from .registry import get_event_handlers
from .types import Handler
from .utils import dialect_resolver


class SQLAlchemyEvents:

    CHANNEL = 'sqlalchemy_events'

    INITIAL_RETRY_DELAY = 1
    MAX_RETRY_DELAY = 30

    def __init__(
        self,
        engine: Union[AsyncEngine, Engine],
        autodiscover_paths: list[str | Path],
        schema: str = '',
        logger: Optional[logging.Logger] = None,
        verbose: bool = True,
    ) -> None:
        self.engine = engine
        self.autodiscover_paths = autodiscover_paths
        self.schema = schema
        self.logger = logger or DefaultLogger() if verbose else None
        self.verbose = verbose

        self._stop_event = asyncio.Event()
        self._listener_task: asyncio.Task | None = None

    async def __call__(self) -> None:
        if not isinstance(self.engine, (AsyncEngine, Engine)):
            raise RuntimeError(
                '[SQLAlchemyEvents] \'engine\' must be an instance of '
                'sqlalchemy.Engine or sqlalchemy.ext.asyncio.AsyncEngine.'
            )

        if not self.autodiscover_paths:
            if self.verbose:
                self.logger.warning(
                    '[SQLAlchemyEvents] No autodiscover paths specified. '
                    'Please provide at least one module path '
                    '(e.g., \'app.handlers\') in \'autodiscover_paths\' '
                    'during initialization.'
                )
            return

        handlers = await self.__find_handlers()

        if not handlers:
            return

        dialect = dialect_resolver(self.engine)

        event_strategy = sa_events_strategy.get(dialect)

        if not event_strategy:
            raise RuntimeError(
                f'[SQLAlchemyEvents] Unsupported database {dialect}. '
                f'This library supports only '
                f'{", ".join(sa_events_strategy.keys())}'
            )

        await self.__start_listen(event_strategy, handlers)

    async def stop(self) -> None:
        self._stop_event.set()

        if self._listener_task is None:
            return

        self._listener_task.cancel()

        try:
            await self._listener_task
        except asyncio.CancelledError:
            pass
        finally:
            self._listener_task = None

    async def __find_handlers(self):
        autodiscover(self.autodiscover_paths)

        handlers = get_event_handlers()

        if not handlers:
            if self.verbose:
                self.logger.info(
                    '[SQLAlchemyEvents] No handlers found'
                )
            return

        res_handlers = []

        for handlers_list in handlers.values():
            res_handlers.extend(handlers_list)

        filtered_handlers = []
        handler_paths = set()
        handlers_qty = defaultdict(int)

        for handler in res_handlers:
            file_path = (
                inspect.getsourcefile(handler.func)
                or inspect.getfile(handler.func)
            )

            file_func = Path(file_path)
            file_name = file_func.name

            handler_path = (
                f'{file_func} {handler.func.__name__}'
            )

            if handler_path in handler_paths:
                continue

            handler_paths.add(handler_path)
            filtered_handlers.append(handler)

            handlers_qty[
                f'{file_func.parent.name}/{file_name}'
            ] += 1

        if self.verbose:
            for path, qty in handlers_qty.items():
                self.logger.info(
                    f'[SQLAlchemyEvents] Registered {qty} '
                    f'{"handler" if qty == 1 else "handlers"} '
                    f'from \'{path}\''
                )

        return filtered_handlers

    async def __start_listen(
        self,
        event_strategy: SaEventStrategy,
        handlers: list[Handler],
    ) -> None:
        base = self.__get_base(handlers)

        if not isinstance(self.engine, AsyncEngine):
            raise RuntimeError(
                '[SQLAlchemyEvents] Sync Engine driver does not support '
                'async LISTEN/NOTIFY. Use AsyncEngine with asyncpg'
            )

        async with self.engine.connect() as conn:
            await event_strategy.init_triggers(
                model_list=base.__subclasses__(),
                schema=self.schema,
                conn=conn,
                logger=self.logger,
            )

        if self.verbose:
            self.logger.info(
                '[SQLAlchemyEvents] Database triggers initialized'
            )

        self._stop_event.clear()

        self._listener_task = asyncio.create_task(
            self.__listen_loop(event_strategy),
            name='sqlalchemy-events-listener',
        )

    @staticmethod
    def __get_base(
        handlers: list[Handler],
    ):
        try:
            model = handlers[0].args['model']
            for cls in model.__mro__:
                if (
                        isinstance(cls, type)
                        and issubclass(cls, DeclarativeBase)
                        and DeclarativeBase in cls.__bases__
                ):
                    return cls

        except Exception:
            raise RuntimeError('[SQLAlchemyEvents] No Base found in Registered handlers')

        raise RuntimeError(
            '[SQLAlchemyEvents] No Base found in Registered handlers'
        )

    async def __listen_loop(
        self,
        event_strategy: SaEventStrategy,
    ) -> None:
        retry_delay = self.INITIAL_RETRY_DELAY

        while not self._stop_event.is_set():
            try:
                await self.__listen_once(event_strategy)
                retry_delay = self.INITIAL_RETRY_DELAY
            except asyncio.CancelledError:
                raise

            except Exception as ex:
                if self._stop_event.is_set():
                    break

                if self.verbose:
                    self.logger.error(
                        f'[SQLAlchemyEvents] Listener crashed: {ex} '
                        f'Reconnect in {retry_delay} seconds'
                    )

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=retry_delay,
                    )
                except asyncio.TimeoutError:
                    pass

                retry_delay = min(
                    retry_delay * 2,
                    self.MAX_RETRY_DELAY,
                )

        if self.verbose:
            self.logger.info(
                '[SQLAlchemyEvents] Listener stopped'
            )

    async def __listen_once(
            self,
            event_strategy: SaEventStrategy,
    ) -> None:
        if self.verbose:
            self.logger.info(
                '[SQLAlchemyEvents] Connecting to PostgreSQL listener'
            )

        async with self.engine.connect() as conn:
            raw_conn = await conn.get_raw_connection()
            driver_conn = raw_conn.driver_connection

            if not hasattr(driver_conn, 'add_listener'):
                raise RuntimeError(
                    '[SQLAlchemyEvents] Driver does not support '
                    'LISTEN/NOTIFY'
                )

            await driver_conn.add_listener(
                self.CHANNEL,
                event_strategy.callback.handle,
            )

            if self.verbose:
                self.logger.info(
                    f'[SQLAlchemyEvents] LISTEN {self.CHANNEL} started',
                )

            try:
                await self._wait_connection(driver_conn)

            finally:
                await self.__remove_listener(
                    driver_conn,
                    event_strategy,
                )

    async def _wait_connection(self, driver_conn) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(1)

            if driver_conn.is_closed():
                raise ConnectionError(
                    '[SQLAlchemyEvents] PostgreSQL listener connection closed'
                )

    async def __wait_for_notify(
        self,
        driver_conn,
    ) -> None:
        while not self._stop_event.is_set():
            try:
                await driver_conn.wait_for_notify()

            except asyncio.CancelledError:
                raise

            except Exception as e:
                raise ConnectionError(
                    '[SQLAlchemyEvents] PostgreSQL connection '
                    f'lost while waiting for NOTIFY: {e}'
                ) from e

    async def __remove_listener(
        self,
        driver_conn,
        event_strategy: SaEventStrategy,
    ) -> None:
        remove_listener = getattr(
            driver_conn,
            'remove_listener',
            None,
        )

        if remove_listener is None:
            return

        try:
            result = remove_listener(
                self.CHANNEL,
                event_strategy.callback.handle,
            )

            if inspect.isawaitable(result):
                await result

        except Exception as e:
            if self.verbose:
                self.logger.warning(
                    f'[SQLAlchemyEvents] Failed to remove listener: {e}',
                )