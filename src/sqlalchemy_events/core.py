import asyncio
import logging
from pathlib import Path
from typing import Optional, Type, Union
import inspect

from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase

from .discovery import autodiscover
from .events import SaEventStrategy, sa_events_strategy
from .registry import get_event_handlers
from .utils import dialect_resolver


class SQLAlchemyEvents:

    def __init__(
        self,
        base: Type[DeclarativeBase],
        engine: Union[AsyncEngine, Engine],
        autodiscover_paths: list[str],
        logger: Optional[logging.Logger] = None
    ) -> None:
        self.base = base
        self.engine = engine
        self.autodiscover_paths = autodiscover_paths
        self.logger = logger
        asyncio.create_task(self.__init())

    async def __init(self) -> None:
        if not await self.__find_handlers():
            return

        dialect = dialect_resolver(self.engine)
        event_strategy = sa_events_strategy.get(dialect)
        if not event_strategy:
            raise RuntimeError(f'[SQLAlchemyEvents] Unsupported database {dialect}. '
                               f'This library supports only {', '.join(sa_events_strategy.keys())}')
        await self.__start_listen(event_strategy)

    async def __find_handlers(self):
        autodiscover(self.autodiscover_paths)
        handlers = get_event_handlers()
        if not handlers:
            if self.logger:
                self.logger.info('[SQLAlchemyEvents] No handlers found')
            return
        res_handlers = []
        for handlers_list in handlers.values():
            res_handlers.extend(handlers_list)

        filtered_handlers = []
        handler_paths = set()
        for handler in res_handlers:
            file_path = inspect.getsourcefile(handler.func) or inspect.getfile(handler.func)

            file_func = Path(file_path)
            file_name = file_func.name
            handler_path = f'{file_func} {handler.func.__name__}'
            if handler_path in handler_paths:
                continue

            handler_paths.add(handler_path)
            filtered_handlers.append(handler)
            if self.logger:
                self.logger.info(f'[SQLAlchemyEvents] Registered handler {handler.func.__name__} '
                                 f'from {file_func.parent.name}/{file_name}')

        return filtered_handlers

    async def __start_listen(self, event_strategy: SaEventStrategy):
        if isinstance(self.engine, AsyncEngine):
            async with self.engine.connect() as conn:
                raw_conn = await conn.get_raw_connection()
                driver_conn = raw_conn.driver_connection

                if not hasattr(driver_conn, 'add_listener'):
                    raise RuntimeError('[SQLAlchemyEvents] Driver does not support LISTEN/NOTIFY')

                await event_strategy.init_triggers(
                    model_list=self.base.__subclasses__(),
                    conn=conn,
                    logger=self.logger
                )
            if self.logger:
                self.logger.info('[SQLAlchemyEvents] Start listening')
            await driver_conn.add_listener('sqlalchemy_events', event_strategy.callback.handle)
            return

        elif isinstance(self.engine, Engine):
            sync_engine_error = ('[SQLAlchemyEvents] Sync Engine driver does not support async LISTEN/NOTIFY. '
                                 'Use AsyncEngine with asyncpg or psycopg[async]')
            with self.engine.connect() as conn:
                raw_conn = conn.connection
                driver_conn = getattr(raw_conn, 'driver_connection', raw_conn)

                if not hasattr(driver_conn, 'add_listener'):
                    raise RuntimeError(sync_engine_error)
            result = driver_conn.add_listener('sqlalchemy_events', event_strategy.callback.handle)

            if not asyncio.iscoroutine(result):
                raise RuntimeError(sync_engine_error)
            await event_strategy.init_triggers(
                model_list=self.base.__subclasses__(),
                conn=conn,
                logger=self.logger
            )
            if self.logger:
                self.logger.info('[SQLAlchemyEvents] Start listening')
            await result

            return

        raise TypeError('[SQLAlchemyEvents] engine must be AsyncEngine or Engine')

    async def __listen_loop(self, driver_conn):
        while True:
            try:
                await driver_conn.wait_for_notify()
            except Exception as e:
                if self.logger:
                    self.logger.error(f'[SQLAlchemyEvents] listener crashed: {e}')
                await asyncio.sleep(1)