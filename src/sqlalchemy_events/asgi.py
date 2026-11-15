from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from .core import SQLAlchemyEvents


class SQLAlchemyEventsMiddleware:
    """ASGI middleware that runs the event system with the application."""

    def __init__(self, app: Any, events: SQLAlchemyEvents) -> None:
        self.app = app
        self.events = events

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        if scope['type'] != 'lifespan':
            await self.app(scope, receive, send)
            return

        try:
            await self.events.start()
        except Exception as ex:
            await send(
                {
                    'type': 'lifespan.startup.failed',
                    'message': f'sqlalchemy_events: {ex}',
                }
            )
            return

        try:
            await self.app(scope, receive, send)
        finally:
            await self.events.stop()


@asynccontextmanager
async def sa_events_lifespan(
    events: SQLAlchemyEvents,
) -> AsyncIterator[SQLAlchemyEvents]:
    """Async context manager suitable for ASGI framework lifespans."""

    await events.start()

    try:
        yield events
    finally:
        await events.stop()
