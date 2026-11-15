from __future__ import annotations

import os
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from sqlalchemy_events import SQLAlchemyEvents
from tests import handlers
from tests.test_models import Base

DATABASE_URL = os.environ.get('DATABASE_URL')


@pytest_asyncio.fixture
async def engine() -> AsyncEngine:
    if not DATABASE_URL:
        pytest.skip('DATABASE_URL is not set')

    engine = create_async_engine(DATABASE_URL)

    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text('DROP SCHEMA IF EXISTS public CASCADE'))
        await conn.execute(text('CREATE SCHEMA public'))
        await conn.run_sync(Base.metadata.create_all)


@pytest_asyncio.fixture
async def events_factory(engine: AsyncEngine, db: None):
    instances: list[SQLAlchemyEvents] = []

    async def factory(**kwargs: Any) -> SQLAlchemyEvents:
        kwargs.setdefault('verbose', False)
        kwargs.setdefault('autodiscover_paths', ['tests.handlers'])

        events = SQLAlchemyEvents(engine=engine, **kwargs)
        await events.start()
        instances.append(events)
        return events

    try:
        yield factory
    finally:
        for instance in reversed(instances):
            await instance.stop()


@pytest.fixture(autouse=True)
def clean_calls():
    handlers.clear_calls()
    yield
    handlers.clear_calls()
