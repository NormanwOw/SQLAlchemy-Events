from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from sqlalchemy_events.config import EventsConfig
from sqlalchemy_events.listener import PostgresListener
from sqlalchemy_events.metrics import Metrics


async def test_listener_heartbeat_and_lifecycle(
    engine: AsyncEngine,
    db: None,
    monkeypatch,
) -> None:
    monkeypatch.setattr('sqlalchemy_events.listener.HEARTBEAT_INTERVAL', 0.05)
    monkeypatch.setattr('sqlalchemy_events.listener.RETRY_DELAY', 0.05)
    monkeypatch.setattr('sqlalchemy_events.listener.MAX_RETRY_DELAY', 0.1)

    async def callback(
        connection: Any,
        pid: int,
        channel: str,
        payload: str,
    ) -> None:
        pass

    metrics = Metrics()
    listener = PostgresListener(
        engine=engine,
        channels={'listener_test_channel'},
        config=EventsConfig(),
        logger=None,
        metrics=metrics,
        callback=callback,
    )

    await listener.start()
    await listener.start()

    assert await listener.wait_ready(2)
    assert listener.connected
    await asyncio.sleep(0.2)

    assert metrics.heartbeats > 0
    assert metrics.listener_connects == 1

    health = await listener.health()

    assert health['connected']
    assert health['channels'] == ['listener_test_channel']

    await listener.stop()

    assert not listener.connected


async def test_wait_ready_times_out_without_start(
    engine: AsyncEngine,
) -> None:
    async def callback(*args: Any) -> None:
        pass

    listener = PostgresListener(
        engine=engine,
        channels=set(),
        config=EventsConfig(),
        logger=None,
        metrics=Metrics(),
        callback=callback,
    )

    assert not await listener.wait_ready(0.01)

    await listener.stop()


async def test_restart_callback_is_scheduled(
    engine: AsyncEngine,
    monkeypatch,
) -> None:
    monkeypatch.setattr('sqlalchemy_events.listener.RETRY_DELAY', 0.01)

    async def callback(*args: Any) -> None:
        pass

    listener = PostgresListener(
        engine=engine,
        channels=set(),
        config=EventsConfig(),
        logger=None,
        metrics=Metrics(),
        callback=callback,
    )

    async def failing() -> None:
        raise RuntimeError('unexpected')

    task = asyncio.create_task(failing())
    await asyncio.gather(task, return_exceptions=True)

    listener._on_done(task)

    assert listener._restart_task is not None

    listener._stopping = True
    listener._restart_task.cancel()
    await asyncio.gather(listener._restart_task, return_exceptions=True)
