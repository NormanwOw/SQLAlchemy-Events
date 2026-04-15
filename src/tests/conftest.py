import os

import pytest_asyncio
from dotenv import load_dotenv

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
from sqlalchemy_events import SQLAlchemyEvents
from sqlalchemy_events.events import sa_events_strategy
from sqlalchemy_events.utils import dialect_resolver

from tests.test_models import Base

load_dotenv()
DATABASE_URL = os.environ.get('DATABASE_URL')


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    yield engine
    await engine.dispose()

IS_DB = False


@pytest_asyncio.fixture(autouse=True)
async def prepared_db(engine):
    global IS_DB
    if not IS_DB:
        async with engine.begin() as conn:
            await conn.execute(text('DROP SCHEMA IF EXISTS public CASCADE'))
            await conn.execute(text('CREATE SCHEMA public'))
            await conn.run_sync(Base.metadata.create_all)

        await SQLAlchemyEvents(
            engine=engine,
            autodiscover_paths=['tests']
        )()
        IS_DB = True


@pytest_asyncio.fixture
async def session(engine):
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def mock_callback(engine):
    dialect = dialect_resolver(engine)
    yield sa_events_strategy[dialect].callback
