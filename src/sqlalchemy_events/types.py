from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from sqlalchemy.orm import DeclarativeBase


class SaEvent(StrEnum):
    INSERT = 'INSERT'
    UPDATE = 'UPDATE'
    DELETE = 'DELETE'

    @classmethod
    def from_value(cls, value: str) -> SaEvent:
        try:
            return cls(value.upper())
        except ValueError as ex:
            raise ValueError(f'Unknown event: {value!r}') from ex


class Dialect(StrEnum):
    POSTGRESQL = 'postgresql'
    SQLITE = 'sqlite'
    MYSQL = 'mysql'
    MSSQL = 'mssql'
    ORACLE = 'oracle'


class SyncExecutorKind(StrEnum):
    THREAD = 'thread'
    PROCESS = 'process'


@dataclass(frozen=True, eq=False)
class Handler:
    """A registered event handler.

    Handlers are compared by their import path, model and event so that
    re-importing a module does not register the same handler twice.
    """

    func: Callable[..., Any]
    model: type[DeclarativeBase]
    event: SaEvent

    @property
    def identity(self) -> str:
        module = getattr(self.func, '__module__', '')
        qualname = getattr(
            self.func,
            '__qualname__',
            getattr(self.func, '__name__', repr(self.func)),
        )
        return f'{module}.{qualname}'

    @property
    def table(self) -> str:
        return str(self.model.__tablename__)

    @property
    def schema(self) -> str | None:
        table = self.model.__table__
        return table.schema

    def __hash__(self) -> int:
        return hash((self.identity, self.table, self.event))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Handler):
            return NotImplemented
        return (
            self.identity,
            self.table,
            self.event,
        ) == (
            other.identity,
            other.table,
            other.event,
        )


@dataclass(frozen=True, slots=True)
class CallPlan:
    """A precomputed calling convention for a handler."""

    handler: Handler
    func: Callable[..., Any]
    is_async: bool
    rows_mode: Literal['keyword', 'positional', 'none']
    rows_position: int | None = None
    owner: type | None = None

    @property
    def needs_rows(self) -> bool:
        return self.rows_mode != 'none'


@dataclass(frozen=True, slots=True)
class Event:
    """A database change event received from PostgreSQL."""

    schema: str
    table: str
    op: SaEvent
    rows: tuple[Any, ...] | None = None
    count: int = 0
    truncated: bool = False

    @property
    def key(self) -> tuple[str, str, SaEvent]:
        return self.schema, self.table, self.op

    def merge(self, other: Event) -> Event:
        if self.key != other.key:
            raise ValueError('Cannot merge events with different keys')

        if self.rows is None and other.rows is None:
            rows = None
        else:
            rows = (self.rows or ()) + (other.rows or ())

        return Event(
            schema=self.schema,
            table=self.table,
            op=self.op,
            rows=rows,
            count=self.count + other.count,
            truncated=self.truncated or other.truncated,
        )

    @classmethod
    def merge_all(cls, events: list[Event]) -> Event:
        if not events:
            raise ValueError('Cannot merge an empty event list')

        merged = events[0]
        for event in events[1:]:
            merged = merged.merge(event)

        return merged


type DB_ID = str | int
