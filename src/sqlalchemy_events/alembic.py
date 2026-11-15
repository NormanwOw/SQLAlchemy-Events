from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase

from .config import EventsConfig
from .defaults import CHANNEL
from .init_triggers_strategies.base import (
    TriggerPlan,
    TriggerTarget,
)
from .init_triggers_strategies.postgres_init_triggers import (
    PostgresInitTriggers,
)
from .utils import advisory_lock_key


def _plans(
    model: type[DeclarativeBase],
    *,
    schema: str,
) -> list[TriggerPlan]:
    config = EventsConfig(schema=schema)
    events = getattr(model, '__events__', None)

    if not events:
        raise ValueError(f'Model {model.__name__} is not decorated with @with_events')

    targets = [
        TriggerTarget(
            model=model,
            event=event,
            needs_rows=True,
        )
        for event in sorted(
            events,
            key=lambda item: item.value,
        )
    ]

    return PostgresInitTriggers().build_plans(
        targets,
        default_schema=schema,
        config=config,
    )


def _current_schema(bind) -> str:
    schema = bind.execute(text('SELECT current_schema()')).scalar()
    return str(schema or 'public')


def _operations(operations):
    if operations is not None:
        return operations

    from alembic import op

    return op


def enable_events(
    model: type[DeclarativeBase],
    *,
    schema: str = '',
    operations=None,
) -> list[str]:
    """Create triggers for a model inside an Alembic migration."""

    op = _operations(operations)
    resolved_schema = schema or _current_schema(op.get_bind())
    plans = _plans(model, schema=resolved_schema)
    config = EventsConfig(schema=resolved_schema)

    statements = PostgresInitTriggers().render_enable(
        plans,
        config=config,
    )

    lock_key = advisory_lock_key(resolved_schema, CHANNEL)
    op.execute(f'SELECT pg_advisory_xact_lock({lock_key})')

    for statement in statements:
        op.execute(statement)

    return statements


def disable_events(
    model: type[DeclarativeBase],
    *,
    schema: str = '',
    operations=None,
) -> list[str]:
    """Drop triggers for a model inside an Alembic migration."""

    op = _operations(operations)
    resolved_schema = schema or _current_schema(op.get_bind())
    plans = _plans(model, schema=resolved_schema)
    statements = PostgresInitTriggers().render_disable(
        plans,
        config=EventsConfig(),
    )

    lock_key = advisory_lock_key(resolved_schema, CHANNEL)
    op.execute(f'SELECT pg_advisory_xact_lock({lock_key})')

    for statement in statements:
        op.execute(statement)

    return statements
