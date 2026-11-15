from __future__ import annotations

from typing import Any

import pytest

from sqlalchemy_events.asgi import (
    SQLAlchemyEventsMiddleware,
    sa_events_lifespan,
)


class FakeEvents:
    def __init__(self, fail_start: bool = False) -> None:
        self.started = 0
        self.stopped = 0
        self.fail_start = fail_start

    async def start(self) -> None:
        self.started += 1
        if self.fail_start:
            raise RuntimeError('startup failed')

    async def stop(self) -> None:
        self.stopped += 1


async def test_lifespan_middleware() -> None:
    events = FakeEvents()
    sent: list[dict[str, Any]] = []
    received = [
        {'type': 'lifespan.startup'},
        {'type': 'lifespan.shutdown'},
    ]

    async def app(scope, receive, send) -> None:
        message = await receive()
        assert message['type'] == 'lifespan.startup'
        await send({'type': 'lifespan.startup.complete'})
        message = await receive()
        assert message['type'] == 'lifespan.shutdown'
        await send({'type': 'lifespan.shutdown.complete'})

    async def receive() -> dict[str, Any]:
        return received.pop(0)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = SQLAlchemyEventsMiddleware(app, events)  # type: ignore[arg-type]
    await middleware({'type': 'lifespan'}, receive, send)

    assert events.started == 1
    assert events.stopped == 1
    assert [message['type'] for message in sent] == [
        'lifespan.startup.complete',
        'lifespan.shutdown.complete',
    ]


async def test_lifespan_startup_failure() -> None:
    events = FakeEvents(fail_start=True)
    sent: list[dict[str, Any]] = []

    async def app(scope, receive, send) -> None:
        raise AssertionError('app must not be called')

    async def receive() -> dict[str, Any]:
        return {'type': 'lifespan.startup'}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = SQLAlchemyEventsMiddleware(app, events)  # type: ignore[arg-type]
    await middleware({'type': 'lifespan'}, receive, send)

    assert events.started == 1
    assert events.stopped == 0
    assert sent[0]['type'] == 'lifespan.startup.failed'
    assert 'startup failed' in sent[0]['message']


async def test_non_lifespan_requests_pass_through() -> None:
    events = FakeEvents()
    called: list[dict[str, Any]] = []

    async def app(scope, receive, send) -> None:
        called.append(scope)

    middleware = SQLAlchemyEventsMiddleware(app, events)  # type: ignore[arg-type]
    await middleware({'type': 'http'}, None, None)

    assert called == [{'type': 'http'}]
    assert events.started == 0


async def test_lifespan_context_manager() -> None:
    events = FakeEvents()

    async with sa_events_lifespan(events) as started:  # type: ignore[arg-type]
        assert started is events
        assert events.started == 1

    assert events.stopped == 1


async def test_lifespan_context_manager_stops_on_error() -> None:
    events = FakeEvents()

    with pytest.raises(RuntimeError):
        async with sa_events_lifespan(events):  # type: ignore[arg-type]
            raise RuntimeError('boom')

    assert events.stopped == 1
