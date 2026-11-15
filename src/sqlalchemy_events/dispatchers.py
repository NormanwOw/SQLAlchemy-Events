from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections import deque
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from .config import EventsConfig
from .defaults import (
    COALESCE_MAX_EVENTS,
    COALESCE_WINDOW,
    QUEUE_MAXSIZE,
    QUEUE_PUT_TIMEOUT,
    SHUTDOWN_TIMEOUT,
    default_worker_count,
)
from .exceptions import NotificationPayloadError
from .execution import HandlerExecutor, LoggerLike
from .metrics import Metrics, utcnow
from .registry import HandlerRegistry
from .types import Event, SaEvent


def parse_notification(
    payload: str,
    *,
    default_schema: str = '',
) -> Event:
    """Parse a LISTEN/NOTIFY payload produced by the library triggers."""

    if not payload:
        raise NotificationPayloadError('Empty notification payload')

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as ex:
        raise NotificationPayloadError(f'Invalid notification payload: {ex}') from ex

    if not isinstance(data, dict):
        raise NotificationPayloadError('Notification payload must be a JSON object')

    version = data.get('v')

    if version not in (None, 1, 2):
        raise NotificationPayloadError(f'Unsupported notification payload version: {version!r}')

    table = data.get('table')
    op = data.get('op')

    if not table or not op:
        raise NotificationPayloadError('Notification payload has no table or operation')

    try:
        event = SaEvent.from_value(str(op))
    except ValueError as ex:
        raise NotificationPayloadError(str(ex)) from ex

    rows_raw = data.get('rows')
    if rows_raw is None:
        rows: tuple[Any, ...] | None = None
    elif isinstance(rows_raw, list):
        rows = tuple(rows_raw)
    else:
        raise NotificationPayloadError('Notification payload rows must be a list')

    schema = str(data.get('schema') or default_schema or 'public')
    count_raw = data.get('count')

    if count_raw is None:
        count = len(rows or ())
    else:
        try:
            count = int(count_raw)
        except (TypeError, ValueError) as ex:
            raise NotificationPayloadError('Notification payload count must be an integer') from ex

    return Event(
        schema=schema,
        table=str(table),
        op=event,
        rows=rows,
        count=count,
        truncated=bool(data.get('truncated', False)),
    )


class BaseDispatcher(ABC):
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        registry: HandlerRegistry,
        config: EventsConfig,
        default_schema: str,
        logger: LoggerLike | None,
        metrics: Metrics,
    ) -> None:
        self.engine = engine
        self.registry = registry
        self.config = config
        self.default_schema = default_schema
        self.logger = logger
        self.metrics = metrics
        self.executor = HandlerExecutor(config, logger, metrics)
        self.executor.register(registry.all())

    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def on_notification(
        self,
        connection: Any,
        pid: int,
        channel: str,
        payload: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        raise NotImplementedError

    def _record_notification(self) -> None:
        self.metrics.notifications_received += 1
        self.metrics.last_notification_at = utcnow()

    def _log_error(self, message: str, exc_info: bool = False) -> None:
        if self.logger is not None:
            self.logger.error(message, exc_info=exc_info)

    def _log_warning(self, message: str) -> None:
        if self.logger is not None:
            self.logger.warning(message)


class NotifyDispatcher(BaseDispatcher):
    """Dispatches events received through LISTEN/NOTIFY."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._buffer: deque[Event] = deque()
        self._workers: list[asyncio.Task[None]] = []
        self._stopping = False

    async def start(self) -> None:
        self._stopping = False
        self._workers = [
            asyncio.create_task(
                self._worker(index),
                name=f'sqlalchemy-events-worker-{index}',
            )
            for index in range(default_worker_count())
        ]

    async def stop(self) -> None:
        self._stopping = True

        for _ in self._workers:
            try:
                await asyncio.wait_for(
                    self._queue.put(None),
                    timeout=QUEUE_PUT_TIMEOUT,
                )
            except TimeoutError:
                break

        if self._workers:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._workers),
                    timeout=SHUTDOWN_TIMEOUT,
                )
            except TimeoutError:
                for worker in self._workers:
                    worker.cancel()
                await asyncio.gather(*self._workers, return_exceptions=True)
            except asyncio.CancelledError:
                pass

        self._workers.clear()
        self.executor.shutdown()

    async def on_notification(
        self,
        connection: Any,
        pid: int,
        channel: str,
        payload: str,
    ) -> None:
        self._record_notification()

        try:
            event = parse_notification(
                payload,
                default_schema=self.default_schema,
            )
        except NotificationPayloadError as ex:
            self.metrics.notifications_invalid += 1
            self.metrics.record_error(ex)
            self._log_warning(
                f'[SQLAlchemyEvents] Ignoring invalid notification on channel {channel!r}: {ex}'
            )
            return

        if not self.registry.has_handlers(event.schema, event.table, event.op):
            self.metrics.events_skipped += 1
            return

        try:
            if QUEUE_PUT_TIMEOUT <= 0:
                self._queue.put_nowait(event)
            else:
                await asyncio.wait_for(
                    self._queue.put(event),
                    timeout=QUEUE_PUT_TIMEOUT,
                )
        except (TimeoutError, asyncio.QueueFull):
            self.metrics.events_dropped += 1
            self.metrics.record_error('Notification queue is full, event dropped')
            self._log_error(
                '[SQLAlchemyEvents] Notification queue is full; dropping '
                f'event for {event.schema}.{event.table} '
                f'({event.op.value.lower()}). '
                'Handlers are too slow or the event rate is too high.'
            )

    async def _next_event(self) -> Event | None:
        if self._buffer:
            return self._buffer.popleft()
        return await self._queue.get()

    async def _worker(self, index: int) -> None:
        while True:
            event = await self._next_event()

            if event is None:
                break

            batch = await self._collect(event)
            merged = Event.merge_all(batch)
            handlers = self.registry.get_handlers(
                merged.schema,
                merged.table,
                merged.op,
            )

            if not handlers:
                self.metrics.events_skipped += 1
                continue

            try:
                failures = await self.executor.execute(merged, handlers)
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                self.metrics.record_error(ex)
                self._log_error(
                    '[SQLAlchemyEvents] Unexpected dispatcher error for '
                    f'{merged.schema}.{merged.table} '
                    f'({merged.op.value.lower()})',
                    exc_info=True,
                )
                continue

            self.metrics.events_dispatched += 1
            self.metrics.last_event_at = utcnow()

            if failures:
                self.metrics.record_error(failures[0].error)

    async def _collect(self, first: Event) -> list[Event]:
        max_events = COALESCE_MAX_EVENTS

        if max_events <= 1:
            return [first]

        batch = [first]
        key = first.key

        remaining: deque[Event] = deque()
        while self._buffer and len(batch) < max_events:
            item = self._buffer.popleft()
            if item.key == key:
                batch.append(item)
            else:
                remaining.append(item)
        self._buffer = remaining

        attempts = 0
        while len(batch) < max_events and attempts < max_events * 4:
            attempts += 1
            try:
                queued: Event | None = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            if queued is None:
                await self._queue.put(None)
                break

            if queued.key == key:
                batch.append(queued)
            else:
                self._buffer.append(queued)

        window = COALESCE_WINDOW
        if window > 0 and len(batch) < max_events:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + window

            while len(batch) < max_events:
                timeout = deadline - loop.time()
                if timeout <= 0:
                    break

                try:
                    queued = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=timeout,
                    )
                except TimeoutError:
                    break

                if queued is None:
                    await self._queue.put(None)
                    break

                if queued.key == key:
                    batch.append(queued)
                else:
                    self._buffer.append(queued)

        return batch

    async def health(self) -> dict[str, Any]:
        return {
            'mode': 'notify',
            'queue_size': self._queue.qsize(),
            'buffer_size': len(self._buffer),
            'workers': len(self._workers),
        }
