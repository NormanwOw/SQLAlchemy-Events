from __future__ import annotations

import hashlib
import random

from sqlalchemy import Engine, inspect
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql.compiler import IdentifierPreparer

from .exceptions import InvalidModelEventError, UnsupportedDialectError
from .types import Dialect, SaEvent

MAX_IDENTIFIER_BYTES = 63


def dialect_resolver(engine: AsyncEngine | Engine) -> Dialect:
    if isinstance(engine, AsyncEngine):
        dialect_name = engine.sync_engine.dialect.name.lower()
    else:
        dialect_name = engine.dialect.name.lower()

    if dialect_name.startswith('postgres'):
        return Dialect.POSTGRESQL
    if dialect_name.startswith('sqlite'):
        return Dialect.SQLITE
    if dialect_name.startswith('mysql'):
        return Dialect.MYSQL
    if dialect_name in ('mssql', 'sqlserver'):
        return Dialect.MSSQL
    if dialect_name.startswith('oracle'):
        return Dialect.ORACLE

    raise UnsupportedDialectError(f'Unsupported dialect: {dialect_name}')


def model_table_name(model: type[DeclarativeBase]) -> str:
    table_name = getattr(model, '__tablename__', None)

    if not table_name:
        raise InvalidModelEventError(f'Model {model.__name__} has no __tablename__')

    return str(table_name)


def model_schema(model: type[DeclarativeBase]) -> str | None:
    table = model.__table__
    return table.schema


def resolve_id_columns(model: type[DeclarativeBase]) -> tuple[str, ...]:
    """Resolve the column names used in event payloads.

    By default the primary key columns are used. Set ``__events_id__`` on
    the model to override this.
    """

    raw = getattr(model, '__events_id__', None)
    table = model.__table__
    available = {column.name for column in table.columns}

    if raw is None:
        columns = tuple(column.name for column in inspect(model).primary_key)
    elif isinstance(raw, str):
        columns = (raw,)
    else:
        try:
            columns = tuple(str(column) for column in raw)
        except TypeError as ex:
            raise InvalidModelEventError(
                f'__events_id__ of {model.__name__} must be a string or an iterable of strings'
            ) from ex

    if not columns:
        raise InvalidModelEventError(f'Model {model.__name__} has no primary key columns')

    missing = [column for column in columns if column not in available]
    if missing:
        raise InvalidModelEventError(
            f'Model {model.__name__} does not have event id columns: {", ".join(missing)}'
        )

    return columns


def quote_identifier(preparer: IdentifierPreparer, identifier: str) -> str:
    return preparer.quote(identifier)


def qualified_name(
    preparer: IdentifierPreparer,
    schema: str | None,
    table: str,
) -> str:
    if schema:
        return f'{preparer.quote(schema)}.{preparer.quote(table)}'
    return preparer.quote(table)


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def stable_hash(*parts: str, length: int = 8) -> str:
    payload = '\x1f'.join(parts).encode()
    return hashlib.sha1(payload, usedforsecurity=False).hexdigest()[:length]


def clamp_identifier(identifier: str, *, source: str | None = None) -> str:
    """Clamp an identifier to PostgreSQL's 63-byte limit."""

    if len(identifier.encode()) <= MAX_IDENTIFIER_BYTES:
        return identifier

    suffix = f'_{stable_hash(source or identifier)}'
    budget = MAX_IDENTIFIER_BYTES - len(suffix.encode())
    trimmed = identifier

    while len(trimmed.encode()) > budget:
        trimmed = trimmed[:-1]

    return trimmed + suffix


def make_trigger_name(table: str, event: SaEvent) -> str:
    name = f'sa_{table}_{event.value.lower()}_notify'
    return clamp_identifier(name, source=f'trigger:{table}:{event.value}')


def make_function_name(
    event: SaEvent,
    *,
    needs_rows: bool,
) -> str:
    rows_suffix = '_rows' if needs_rows else ''
    event_name = event.value.lower()
    name = f'sqlalchemy_events_notify_{event_name}{rows_suffix}'

    return clamp_identifier(name, source=f'function:{event}')


def backoff_delay(
    attempt: int,
    base_delay: float,
    max_delay: float,
) -> float:
    """Full-jitter exponential backoff."""

    if base_delay <= 0:
        return 0.0

    ceiling = min(max_delay, base_delay * (2 ** max(attempt, 0)))
    return random.uniform(0, ceiling)


def advisory_lock_key(schema: str, channel: str) -> int:
    value = int(
        stable_hash(
            'sync',
            schema,
            channel,
            length=16,
        ),
        16,
    )

    if value >= 2**63:
        value -= 2**64

    return value


def model_source_path(model: type[DeclarativeBase]) -> str | None:
    module = getattr(model, '__module__', None)
    if not module:
        return None
    return module.replace('.', '/')
