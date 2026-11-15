from __future__ import annotations

import asyncio
import random
from collections.abc import Iterable
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from .config import EventsConfig
from .defaults import (
    HEARTBEAT_INTERVAL,
    LISTENER_APPLICATION_NAME,
    MAX_RETRY_DELAY,
    RETRY_DELAY,
)
from .execution import LoggerLike
from .metrics import Metrics
from .utils import backoff_delay


class PostgresListener:
    """Maintains a resilient LISTEN connection to PostgreSQL."""

    LOG_INTERVAL = 30.0

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        channels: Iterable[str],
        config: EventsConfig,
        logger: LoggerLike | None,
        metrics: Metrics,
        callback: Any,
    ) -> None:
        self.engine = engine
        self.channels = set(channels)
        self.config = config
        self.logger = logger
        self.metrics = metrics
        self.callback = callback

        self._stop = asyncio.Event()
        self._ready = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._restart_task: asyncio.Task[None] | None = None
        self._connected = False
        self._generation = 0
        self._stopping = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def generation(self) -> int:
        return self._generation

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return

        self._stopping = False
        self._stop.clear()
        self._ready.clear()
        self._spawn()

    def _spawn(self) -> None:
        self._task = asyncio.create_task(
            self._run(),
            name='sqlalchemy-events-listener',
        )
        self._task.add_done_callback(self._on_done)

    async def wait_ready(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
        except TimeoutError:
            return False
        return True

    async def stop(self) -> None:
        self._stopping = True
        self._stop.set()
        self._ready.set()

        task = self._task
        restart_task = self._restart_task
        self._task = None
        self._restart_task = None

        for pending in (task, restart_task):
            if pending is None:
                continue
            pending.cancel()
            try:
                await pending
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        self._connected = False

    def _on_done(self, task: asyncio.Task[None]) -> None:
        self._task = None

        if self._stopping or self._stop.is_set():
            return

        if task.cancelled():
            return

        error = task.exception()
        self.metrics.listener_failures += 1

        if error is not None:
            self.metrics.record_error(error)
            if self.logger is not None:
                self.logger.error(
                    '[SQLAlchemyEvents] Listener task stopped unexpectedly',
                    exc_info=True,
                )
        elif self.logger is not None:
            self.logger.error(
                '[SQLAlchemyEvents] Listener task stopped unexpectedly without an error'
            )

        self._restart_task = asyncio.create_task(
            self._restart(),
            name='sqlalchemy-events-listener-restart',
        )

    async def _restart(self) -> None:
        await self._sleep(RETRY_DELAY)

        if not self._stopping:
            self._spawn()

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(
                self._stop.wait(),
                timeout=seconds,
            )
        except TimeoutError:
            pass

    async def _run(self) -> None:
        attempt = 0
        last_log_time = 0.0

        while not self._stop.is_set():
            generation = self._generation

            try:
                await self._listen_once()
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                if self._stop.is_set():
                    break

                self.metrics.listener_failures += 1
                self.metrics.record_error(ex)

                if self._generation != generation:
                    attempt = 0

                delay = backoff_delay(
                    attempt,
                    RETRY_DELAY,
                    MAX_RETRY_DELAY,
                )
                attempt = min(attempt + 1, 20)

                now = asyncio.get_running_loop().time()
                if last_log_time == 0.0 or now - last_log_time >= self.LOG_INTERVAL:
                    if self.logger is not None:
                        self.logger.warning(
                            '[SQLAlchemyEvents] Listener connection failed: '
                            f'{ex}. Reconnecting in {delay:.2f}s '
                            f'(attempt {attempt})'
                        )
                    last_log_time = now

                await self._sleep(delay)
            else:
                if self._stop.is_set():
                    break

                attempt = 0
                await self._sleep(RETRY_DELAY * random.uniform(0.5, 1.5))

    async def _listen_once(self) -> None:
        connect_args: dict[str, Any] = {}

        if self.engine.dialect.driver == 'asyncpg':
            connect_args['server_settings'] = {
                'application_name': LISTENER_APPLICATION_NAME,
            }

        listener_engine = create_async_engine(
            self.engine.url,
            poolclass=NullPool,
            connect_args=connect_args,
        )

        connected = False

        try:
            async with listener_engine.connect() as conn:
                raw_conn = await conn.get_raw_connection()
                driver_conn = cast(Any, raw_conn.driver_connection)

                if not hasattr(driver_conn, 'add_listener'):
                    raise RuntimeError(
                        '[SQLAlchemyEvents] Driver does not support LISTEN/NOTIFY; use asyncpg'
                    )

                disconnected = asyncio.Event()

                def on_disconnect(connection: Any) -> None:
                    disconnected.set()

                driver_conn.add_termination_listener(on_disconnect)

                try:
                    for channel in sorted(self.channels):
                        await driver_conn.add_listener(
                            channel,
                            self.callback,
                        )

                    connected = True
                    self._connected = True
                    self._generation += 1
                    self.metrics.listener_connects += 1
                    self._ready.set()

                    if self.logger is not None:
                        self.logger.info('[SQLAlchemyEvents] Listener connected')

                    while not self._stop.is_set():
                        try:
                            await asyncio.wait_for(
                                disconnected.wait(),
                                timeout=HEARTBEAT_INTERVAL,
                            )
                        except TimeoutError:
                            await self._heartbeat(conn)
                            self.metrics.heartbeats += 1
                        else:
                            if self._stop.is_set():
                                break
                            raise ConnectionError('Database connection lost')

                finally:
                    try:
                        driver_conn.remove_termination_listener(on_disconnect)
                    except Exception:
                        pass

                    for channel in sorted(self.channels):
                        remove_listener = getattr(
                            driver_conn,
                            'remove_listener',
                            None,
                        )
                        if remove_listener is None:
                            continue
                        try:
                            result = remove_listener(
                                channel,
                                self.callback,
                            )
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception:
                            pass

        finally:
            if connected:
                self.metrics.listener_disconnects += 1
            self._connected = False
            await listener_engine.dispose()

    async def _heartbeat(self, conn: Any) -> None:
        try:
            await asyncio.wait_for(
                conn.execute(text('SELECT 1')),
                timeout=HEARTBEAT_INTERVAL,
            )
            await conn.rollback()
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            raise ConnectionError(f'Listener heartbeat failed: {ex}') from ex

    async def health(self) -> dict[str, Any]:
        return {
            'connected': self._connected,
            'channels': sorted(self.channels),
            'generation': self._generation,
        }
