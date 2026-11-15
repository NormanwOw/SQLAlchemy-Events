from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import pytest
import pytest_asyncio
from sqlalchemy import Column, Integer, create_engine, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from sqlalchemy_events import SaEvent, SQLAlchemyEvents, with_events
from sqlalchemy_events.exceptions import EventHandlerConfigurationError
from sqlalchemy_events.registry import HandlerRegistry
from sqlalchemy_events.types import Handler
from tests import handlers
from tests.test_models import Base, PlainModel, UserModel


async def wait_until(
    predicate: Callable[[], bool],
    timeout: float = 5.0,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError('Condition was not met in time')
        await asyncio.sleep(0.01)


@pytest_asyncio.fixture
async def dummy_engine() -> AsyncEngine:
    engine = create_async_engine('postgresql+asyncpg://user:password@127.0.0.1:1/db')

    try:
        yield engine
    finally:
        await engine.dispose()


async def test_invalid_engine_type() -> None:
    events = SQLAlchemyEvents(
        engine='not_engine',  # type: ignore[arg-type]
        autodiscover_paths=['tests.handlers'],
    )

    with pytest.raises(RuntimeError):
        await events.start()


async def test_sync_engine_is_rejected() -> None:
    engine = create_engine('sqlite://')
    events = SQLAlchemyEvents(
        engine=engine,
        autodiscover_paths=['tests.handlers'],
    )

    try:
        with pytest.raises(RuntimeError):
            await events.start()
    finally:
        engine.dispose()


async def test_empty_paths_logs_warning(
    dummy_engine: AsyncEngine,
    caplog,
) -> None:
    logger = logging.getLogger('sa-events-empty-paths')
    caplog.set_level(logging.WARNING, logger='sa-events-empty-paths')

    events = SQLAlchemyEvents(
        engine=dummy_engine,
        autodiscover_paths=[],
        logger=logger,
        verbose=True,
    )
    await events.start()

    assert 'No autodiscover paths' in caplog.text
    assert not events.is_connected


async def test_no_logs_when_verbose_disabled(
    dummy_engine: AsyncEngine,
    caplog,
) -> None:
    events = SQLAlchemyEvents(
        engine=dummy_engine,
        autodiscover_paths=[],
        verbose=False,
    )
    await events.start()

    assert caplog.text == ''


async def test_custom_logger_is_used(
    dummy_engine: AsyncEngine,
    caplog,
) -> None:
    logger = logging.getLogger('sa-events-custom')
    caplog.set_level(logging.WARNING, logger='sa-events-custom')

    events = SQLAlchemyEvents(
        engine=dummy_engine,
        autodiscover_paths=[],
        logger=logger,
    )
    await events.start()

    assert 'No autodiscover paths' in caplog.text


async def test_no_handlers_found(
    dummy_engine: AsyncEngine,
    monkeypatch,
) -> None:
    registry = HandlerRegistry()
    monkeypatch.setattr(
        'sqlalchemy_events.core.get_event_handlers',
        lambda: registry,
    )

    events = SQLAlchemyEvents(
        engine=dummy_engine,
        autodiscover_paths=['tests.no_handlers'],
        verbose=False,
    )
    await events.start()

    assert not events.is_healthy
    health = await events.health()
    assert not health.healthy


async def test_undecorated_model_is_rejected(
    dummy_engine: AsyncEngine,
) -> None:
    events = SQLAlchemyEvents(
        engine=dummy_engine,
        autodiscover_paths=['tests.no_handlers'],
        verbose=False,
    )

    def handler() -> None:
        pass

    event_handler = Handler(
        func=handler,
        model=PlainModel,
        event=SaEvent.INSERT,
    )

    with pytest.raises(EventHandlerConfigurationError):
        events._validate_handlers((event_handler,))


async def test_disabled_event_is_rejected(
    dummy_engine: AsyncEngine,
) -> None:
    @with_events([SaEvent.INSERT])
    class InsertOnly(Base):
        __tablename__ = 'insert_only'

        id = Column(Integer, primary_key=True)

    def handler() -> None:
        pass

    events = SQLAlchemyEvents(
        engine=dummy_engine,
        autodiscover_paths=['tests.no_handlers'],
        verbose=False,
    )
    event_handler = Handler(
        func=handler,
        model=InsertOnly,
        event=SaEvent.UPDATE,
    )

    with pytest.raises(EventHandlerConfigurationError):
        events._validate_handlers((event_handler,))


async def test_lifecycle_and_health(
    engine: AsyncEngine,
    events_factory,
) -> None:
    events = await events_factory()

    assert events.is_connected
    assert events.is_healthy

    health = await events.health()

    assert health.healthy
    assert health.connected
    assert health.details['schema'] == 'public'

    await events.start()

    assert events.is_connected

    metrics_text = events.prometheus_metrics()
    assert 'sqlalchemy_events_notifications_received' in metrics_text
    assert 'sqlalchemy_events_handler_calls' in metrics_text

    await events.stop()
    await events.stop()

    assert not events.is_connected
    health = await events.health()
    assert not health.healthy


async def test_context_manager(
    engine: AsyncEngine,
    db: None,
) -> None:
    events = SQLAlchemyEvents(
        engine=engine,
        autodiscover_paths=['tests.handlers'],
        verbose=False,
    )

    async with events as started:
        assert started is events
        assert events.is_connected

    assert not events.is_connected


async def test_listener_reconnects(
    engine: AsyncEngine,
    events_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr('sqlalchemy_events.listener.RETRY_DELAY', 0.1)
    monkeypatch.setattr('sqlalchemy_events.listener.MAX_RETRY_DELAY', 0.5)
    monkeypatch.setattr('sqlalchemy_events.listener.HEARTBEAT_INTERVAL', 5)

    events = await events_factory()

    async with engine.connect() as conn:
        pid = await conn.scalar(
            text(
                'SELECT pid FROM pg_stat_activity WHERE application_name = '
                "'sqlalchemy_events_listener'"
            )
        )
    assert pid

    async with engine.begin() as conn:
        await conn.execute(
            text('SELECT pg_terminate_backend(:pid)'),
            {'pid': pid},
        )

    await wait_until(lambda: not events.is_connected, timeout=3)
    await wait_until(lambda: events.is_connected, timeout=5)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        session.add(UserModel(name='after-reconnect'))
        await session.commit()

    calls = await handlers.wait_for_calls('user:insert')

    assert calls == [[1]]


async def test_health_details_include_configuration(
    engine: AsyncEngine,
    events_factory,
) -> None:
    events = await events_factory()

    health = await events.health()

    assert health.details['listener']['channels'] == ['sqlalchemy_events']
    assert health.details['plan_count'] > 0


async def test_notify_mode_duplicates_events_across_instances(
    engine: AsyncEngine,
    events_factory,
) -> None:
    """NOTIFY is broadcast: every instance executes the same handler."""

    await events_factory()
    await events_factory()

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        session.add(UserModel(name='broadcast'))
        await session.commit()

    calls = await handlers.wait_for_calls('user:insert', count=2)

    assert calls == [[1], [1]]
