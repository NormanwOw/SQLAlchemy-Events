import multiprocessing as mp
import os
from concurrent.futures import ProcessPoolExecutor
from typing import Union

from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from .types import Dialect


def dialect_resolver(engine: Union[AsyncEngine, Engine]) -> Dialect:
    if isinstance(engine, AsyncEngine):
        dialect_name = engine.sync_engine.dialect.name.lower()
    else:
        dialect_name = engine.dialect.name.lower()

    if dialect_name.startswith('postgres'):
        return Dialect.POSTGRESQL
    elif dialect_name.startswith('sqlite'):
        return Dialect.SQLITE
    elif dialect_name.startswith('mysql'):
        return Dialect.MYSQL
    elif dialect_name in ('mssql', 'sqlserver'):
        return Dialect.MSSQL
    elif dialect_name.startswith('oracle'):
        return Dialect.ORACLE

    raise ValueError(f'Unsupported dialect: {dialect_name}')


_process_pool = ProcessPoolExecutor(
    max_workers=max(1, os.cpu_count() - 1),
    mp_context=mp.get_context('spawn'),
)
