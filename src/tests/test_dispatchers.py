from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from sqlalchemy_events.config import EventsConfig
from sqlalchemy_events.dispatchers import NotifyDispatcher
from sqlalchemy_events.metrics import Metrics
from sqlalchemy_events.registry import HandlerRegistry
from sqlalchemy_events.types import Handler, SaEvent
from tests.test_models import MixedCaseModel, UserModel


@pytest.fixture(autouse=True)
def single_worker(monkeypatch) -> None:
    monkeypatch.setattr(
        'sqlalchemy_events.dispatchers.default_worker_count',
        lambda: 1,
    )


async def wait_until(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError('Condition was not met in time')
        await asyncio.sleep(0.01)


def payload(
    table: str,
    rows: list[Any],
    *,
    op: str = 'insert',
    schema: str = 'public',
) -> str:
    return json.dumps(
        {
            'v': 2,
            'op': op,
            'schema': schema,
            'table': table,
            'count': len(rows),
            'rows': rows,
        }
    )


def make_dispatcher(
    registry: HandlerRegistry,
    config: EventsConfig,
) -> NotifyDispatcher:
    return NotifyDispatcher(
        engine=None,  # type: ignore[arg-type]
        registry=registry,
        config=config,
        default_schema='public',
        logger=None,
        metrics=Metrics(),
    )


def make_recorder():
    calls: list[list[Any]] = []

    async def handler(rows) -> None:
        calls.append(rows)

    return handler, calls


async def test_notify_dispatch_and_coalescing(monkeypatch) -> None:
    monkeypatch.setattr('sqlalchemy_events.dispatchers.COALESCE_WINDOW', 0.1)
    monkeypatch.setattr('sqlalchemy_events.dispatchers.COALESCE_MAX_EVENTS', 10)

    registry = HandlerRegistry()
    handler, calls = make_recorder()
    registry.register(Handler(handler, UserModel, SaEvent.INSERT))

    dispatcher = make_dispatcher(registry, EventsConfig())
    await dispatcher.start()

    try:
        await asyncio.gather(
            dispatcher.on_notification(None, 0, 'ch', payload('users', [1])),
            dispatcher.on_notification(None, 0, 'ch', payload('users', [2])),
        )
        await wait_until(lambda: len(calls) == 1)
    finally:
        await dispatcher.stop()

    assert calls == [[1, 2]]
    assert dispatcher.metrics.notifications_received == 2
    assert dispatcher.metrics.events_dispatched == 1
    assert dispatcher.metrics.handler_calls == 1


async def test_notify_dispatch_separate_tables() -> None:
    registry = HandlerRegistry()
    user_handler, user_calls = make_recorder()
    mixed_handler, mixed_calls = make_recorder()
    registry.register(Handler(user_handler, UserModel, SaEvent.INSERT))
    registry.register(Handler(mixed_handler, MixedCaseModel, SaEvent.INSERT))

    dispatcher = make_dispatcher(registry, EventsConfig())
    await dispatcher.start()

    try:
        await dispatcher.on_notification(
            None,
            0,
            'ch',
            payload('users', [1]),
        )
        await dispatcher.on_notification(
            None,
            0,
            'ch',
            payload('MixedCase', [2]),
        )
        await wait_until(lambda: user_calls and mixed_calls)
    finally:
        await dispatcher.stop()

    assert user_calls == [[1]]
    assert mixed_calls == [[2]]


async def test_notify_invalid_payload_and_no_handlers() -> None:
    registry = HandlerRegistry()
    dispatcher = make_dispatcher(registry, EventsConfig())
    await dispatcher.start()

    try:
        await dispatcher.on_notification(None, 0, 'ch', 'bad payload')
        await dispatcher.on_notification(
            None,
            0,
            'ch',
            payload('users', [1]),
        )
    finally:
        await dispatcher.stop()

    assert dispatcher.metrics.notifications_invalid == 1
    assert dispatcher.metrics.events_skipped == 1


async def test_notify_queue_overflow_is_counted(monkeypatch) -> None:
    monkeypatch.setattr('sqlalchemy_events.dispatchers.QUEUE_MAXSIZE', 1)
    monkeypatch.setattr('sqlalchemy_events.dispatchers.QUEUE_PUT_TIMEOUT', 0.01)
    monkeypatch.setattr('sqlalchemy_events.dispatchers.SHUTDOWN_TIMEOUT', 0.05)

    registry = HandlerRegistry()

    async def slow_handler(rows) -> None:
        await asyncio.sleep(0.5)

    registry.register(Handler(slow_handler, UserModel, SaEvent.INSERT))

    dispatcher = make_dispatcher(registry, EventsConfig())
    await dispatcher.start()

    try:
        await dispatcher.on_notification(
            None,
            0,
            'ch',
            payload('users', [0]),
        )
        await asyncio.sleep(0.05)
        await asyncio.gather(
            *[
                dispatcher.on_notification(
                    None,
                    0,
                    'ch',
                    payload('users', [index]),
                )
                for index in range(1, 6)
            ]
        )
    finally:
        await dispatcher.stop()

    assert dispatcher.metrics.events_dropped > 0
