from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from sqlalchemy_events import EventsConfig, SQLAlchemyEvents
from sqlalchemy_events.init_triggers_strategies.base import TriggerTarget
from sqlalchemy_events.init_triggers_strategies.postgres_init_triggers import (
    PostgresInitTriggers,
)
from sqlalchemy_events.utils import advisory_lock_key
from tests import handlers
from tests.test_models import (
    CompositeModel,
    CustomIdModel,
    MixedCaseModel,
    SyncModel,
    UserModel,
    UuidModel,
)


async def trigger_names(engine: AsyncEngine, table: str) -> set[str]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                'SELECT t.tgname FROM pg_trigger t '
                'JOIN pg_class c ON c.oid = t.tgrelid '
                'JOIN pg_namespace n ON n.oid = c.relnamespace '
                'WHERE NOT t.tgisinternal '
                'AND n.nspname = :schema AND c.relname = :table'
            ),
            {'schema': 'public', 'table': table},
        )
        return {row[0] for row in result}


async def function_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                'SELECT p.proname FROM pg_proc p '
                'JOIN pg_namespace n ON n.oid = p.pronamespace '
                "WHERE n.nspname = 'public' "
                "AND p.proname LIKE 'sqlalchemy_events_%'"
            )
        )
        return {row[0] for row in result}


async def insert_rows(
    engine: AsyncEngine,
    *objects: Any,
) -> None:
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        session.add_all(objects)
        await session.commit()


async def test_creates_triggers_and_functions(
    engine: AsyncEngine,
    events_factory,
) -> None:
    await events_factory()

    assert await trigger_names(engine, 'users') == {
        'sa_users_insert_notify',
        'sa_users_update_notify',
        'sa_users_delete_notify',
    }

    functions = await function_names(engine)
    assert 'sqlalchemy_events_notify_insert_rows' in functions
    assert 'sqlalchemy_events_notify_update_rows' in functions
    assert 'sqlalchemy_events_notify_delete_rows' in functions


async def test_sync_is_idempotent(
    engine: AsyncEngine,
    events_factory,
) -> None:
    events = await events_factory()
    await events_factory()

    result = await events.sync_triggers()

    assert result.created_triggers == []
    assert result.replaced_triggers == []
    assert result.functions == []
    assert result.statements == []


async def test_sync_dry_run_does_not_create_objects(
    engine: AsyncEngine,
    db: None,
) -> None:
    events = SQLAlchemyEvents(
        engine=engine,
        autodiscover_paths=['tests.handlers'],
        verbose=False,
        auto_sync=False,
    )

    result = await events.sync_triggers(dry_run=True)

    assert result.created_triggers
    assert result.functions
    assert result.statements
    assert await trigger_names(engine, 'users') == set()


async def test_sync_advisory_lock_blocks_until_released(
    engine: AsyncEngine,
    db: None,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        'sqlalchemy_events.init_triggers_strategies.postgres_init_triggers.ADVISORY_LOCK_TIMEOUT',
        0.2,
    )

    events = SQLAlchemyEvents(
        engine=engine,
        autodiscover_paths=['tests.handlers'],
        verbose=False,
        auto_sync=False,
    )
    key = advisory_lock_key(
        'public',
        'sqlalchemy_events',
    )

    blocker_engine = create_async_engine(engine.url, poolclass=NullPool)

    try:
        async with blocker_engine.connect() as blocker:
            await blocker.execute(
                text('SELECT pg_advisory_lock(:key)'),
                {'key': key},
            )

            with pytest.raises(DBAPIError) as error:
                await events.sync_triggers()

            assert 'timeout' in str(error.value).lower()
            assert await trigger_names(engine, 'users') == set()

            await blocker.execute(
                text('SELECT pg_advisory_unlock(:key)'),
                {'key': key},
            )
            await blocker.rollback()
    finally:
        await blocker_engine.dispose()

    result = await events.sync_triggers()

    assert 'sa_users_insert_notify' in result.created_triggers


async def test_sync_waits_for_advisory_lock(
    engine: AsyncEngine,
    db: None,
) -> None:
    events = SQLAlchemyEvents(
        engine=engine,
        autodiscover_paths=['tests.handlers'],
        verbose=False,
        auto_sync=False,
    )
    key = advisory_lock_key(
        'public',
        'sqlalchemy_events',
    )
    blocker_engine = create_async_engine(engine.url, poolclass=NullPool)

    try:
        async with blocker_engine.connect() as blocker:
            await blocker.execute(
                text('SELECT pg_advisory_lock(:key)'),
                {'key': key},
            )

            task = asyncio.create_task(events.sync_triggers())
            await asyncio.sleep(0.2)

            assert not task.done()

            await blocker.execute(
                text('SELECT pg_advisory_unlock(:key)'),
                {'key': key},
            )
            await blocker.rollback()

            result = await task

            assert 'sa_users_insert_notify' in result.created_triggers
    finally:
        await blocker_engine.dispose()


async def test_sync_advisory_lock_serializes_instances(
    engine: AsyncEngine,
    db: None,
) -> None:
    instances = [
        SQLAlchemyEvents(
            engine=engine,
            autodiscover_paths=['tests.handlers'],
            verbose=False,
            auto_sync=False,
        )
        for _ in range(3)
    ]

    results = await asyncio.gather(*(instance.sync_triggers() for instance in instances))

    assert await trigger_names(engine, 'users') == {
        'sa_users_insert_notify',
        'sa_users_update_notify',
        'sa_users_delete_notify',
    }
    assert sum(len(result.created_triggers) for result in results) == 12
    assert sum(len(result.functions) for result in results) == 3
    assert sum(len(result.replaced_triggers) for result in results) == 0


async def test_event_dispatch_for_mixed_case_table(
    engine: AsyncEngine,
    events_factory,
) -> None:
    await events_factory()
    await insert_rows(engine, MixedCaseModel(id=1))

    assert await handlers.wait_for_calls('mixed:insert') == [[1]]


async def test_composite_primary_key(
    engine: AsyncEngine,
    events_factory,
) -> None:
    await events_factory()
    await insert_rows(engine, CompositeModel(a=1, b=2))

    assert await handlers.wait_for_calls('composite:insert') == [[[1, 2]]]


async def test_custom_id_column(
    engine: AsyncEngine,
    events_factory,
) -> None:
    await events_factory()
    await insert_rows(engine, CustomIdModel(pk=1, external_id=77))

    assert await handlers.wait_for_calls('custom:insert') == [[77]]


async def test_sync_handler_runs(
    engine: AsyncEngine,
    events_factory,
) -> None:
    await events_factory()
    await insert_rows(engine, SyncModel(id=1))

    assert await handlers.wait_for_calls('sync:insert') == [[1]]


async def test_missing_table_is_reported(
    engine: AsyncEngine,
    events_factory,
    caplog,
) -> None:
    logger = logging.getLogger('sa-events-test')
    caplog.set_level(logging.INFO, logger='sa-events-test')

    events = await events_factory(
        verbose=True,
        logger=logger,
        auto_sync=False,
    )

    async with engine.begin() as conn:
        await conn.execute(text('DROP TABLE sync_models'))

    result = await events.sync_triggers()

    assert 'public.sync_models' in result.missing_tables
    assert 'does not exist' in caplog.text


async def test_large_notify_payload_is_truncated(
    engine: AsyncEngine,
    events_factory,
) -> None:
    events = await events_factory()

    ids = [str(uuid.uuid4()) for _ in range(300)]
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        await session.execute(insert(UuidModel).values([{'id': value} for value in ids]))
        await session.commit()

    calls = await handlers.wait_for_calls('uuid:insert')

    assert calls == [[]]
    assert events.metrics.events_truncated == 1


async def test_bulk_insert_above_notify_limit_is_not_aborted(
    engine: AsyncEngine,
    events_factory,
) -> None:
    """Regression test for the old array_agg-based NOTIFY payload.

    The previous implementation put all row ids into a single pg_notify
    payload. A statement inserting more than ~1000 integer rows exceeded
    PostgreSQL's 8000-byte NOTIFY limit, raised "payload string too long"
    and rolled back the whole user transaction.
    """

    events = await events_factory()

    rows = [{'name': f'user-{index}'} for index in range(2000)]
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        await session.execute(insert(UserModel).values(rows))
        await session.commit()

    async with engine.connect() as conn:
        inserted = await conn.scalar(text('SELECT count(*) FROM users'))

    assert inserted == 2000

    calls = await handlers.wait_for_calls('user:insert')

    assert calls == [[]]
    assert events.metrics.events_truncated == 1

    await insert_rows(engine, UserModel(name='after-bulk'))

    calls = await handlers.wait_for_calls('user:insert', count=2)

    assert calls[1] == [2001]


async def test_changed_trigger_is_replaced(
    engine: AsyncEngine,
    events_factory,
) -> None:
    events = await events_factory()

    async with engine.begin() as conn:
        await conn.execute(text('DROP TRIGGER sa_users_insert_notify ON users'))
        await conn.execute(
            text(
                'CREATE TRIGGER sa_users_insert_notify '
                'AFTER INSERT ON users '
                'REFERENCING NEW TABLE AS new_rows '
                'FOR EACH STATEMENT '
                'EXECUTE FUNCTION '
                'public.sqlalchemy_events_notify_insert_rows()'
            )
        )

    result = await events.sync_triggers()

    assert 'sa_users_insert_notify' in result.replaced_triggers


async def test_changed_function_is_recreated(
    engine: AsyncEngine,
    events_factory,
) -> None:
    events = await events_factory()

    async with engine.begin() as conn:
        await conn.execute(
            text("COMMENT ON FUNCTION public.sqlalchemy_events_notify_insert_rows() IS 'outdated'")
        )

    result = await events.sync_triggers()

    assert 'public.sqlalchemy_events_notify_insert_rows' in result.functions


async def test_drop_and_list(
    engine: AsyncEngine,
    events_factory,
) -> None:
    events = await events_factory()
    listed = await events.list_triggers()

    assert any(trigger.trigger == 'sa_users_insert_notify' for trigger in listed)

    await events.stop()

    result = await events.drop_triggers()

    assert result.triggers
    assert result.functions
    assert await trigger_names(engine, 'users') == set()
    assert await events.list_triggers() == []


async def test_render_statements() -> None:
    strategy = PostgresInitTriggers()
    config = EventsConfig()
    targets = [
        TriggerTarget(
            model=UserModel,
            event=event,
            needs_rows=True,
        )
        for event in UserModel.__events__
    ]

    plans = strategy.build_plans(
        targets,
        default_schema='public',
        config=config,
    )
    enabled = strategy.render_enable(plans, config=config)
    disabled = strategy.render_disable(plans, config=config)

    assert any('CREATE OR REPLACE FUNCTION' in sql for sql in enabled)
    assert any('CREATE TRIGGER' in sql for sql in enabled)
    assert len(disabled) == len(plans)
    assert all('DROP TRIGGER' in sql for sql in disabled)
