from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy.orm import DeclarativeBase

from .config import EventsConfig
from .exceptions import HandlerExecutionError
from .execution import HandlerExecutor, HandlerFailure
from .metrics import Metrics
from .registry import HandlerRegistry, get_event_handlers, reset_event_handlers
from .types import Event, SaEvent


@dataclass
class DispatchReport:
    event: Event
    handler_count: int
    failures: list[HandlerFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def _resolve_target(
    model_or_table: type | str,
    schema: str | None,
) -> tuple[str, str]:
    if isinstance(model_or_table, str):
        return schema or 'public', model_or_table

    table = getattr(model_or_table, '__tablename__', None)

    if not table:
        raise ValueError('model_or_table must be a table name or a declarative model')

    model = cast(type[DeclarativeBase], model_or_table)
    table_schema = model.__table__.schema
    return schema or table_schema or 'public', str(table)


async def emit(
    model_or_table: type | str,
    event: SaEvent,
    rows: list[Any] | None = None,
    *,
    schema: str | None = None,
    config: EventsConfig | None = None,
    registry: HandlerRegistry | None = None,
    logger: Any = None,
    raise_on_error: bool = True,
) -> DispatchReport:
    """Dispatch a synthetic event to handlers without a database.

    Intended for unit tests::

        await emit(UserModel, SaEvent.INSERT, rows=[1, 2])
    """

    table_schema, table = _resolve_target(model_or_table, schema)
    handler_registry = registry or get_event_handlers()
    handlers = handler_registry.get_handlers(
        table_schema,
        table,
        event,
    )

    executor_config = config or EventsConfig()

    event_object = Event(
        schema=table_schema,
        table=table,
        op=event,
        rows=None if rows is None else tuple(rows),
        count=len(rows or ()),
    )

    executor = HandlerExecutor(
        executor_config,
        logger,
        Metrics(),
    )

    try:
        executor.register(handlers)
        failures = await executor.execute(event_object, handlers)
    finally:
        executor.shutdown()

    report = DispatchReport(
        event=event_object,
        handler_count=len(handlers),
        failures=failures,
    )

    if failures and raise_on_error:
        first = failures[0]
        raise HandlerExecutionError(f'{first.describe()} failed: {first.error}') from first.error

    return report


def clear_handlers() -> None:
    """Clear the global handler registry (primarily for test isolation)."""

    reset_event_handlers()
