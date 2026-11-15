# SQLAlchemy Events

Event-driven extension for SQLAlchemy that listens to PostgreSQL CUD events.
React to database changes in real time with a declarative API.

- **PostgreSQL only** (PG 10+)
- Async `LISTEN/NOTIFY`
- Handlers as module-level functions, `staticmethod` or `classmethod`
- SQLAlchemy `rows` payloads, composite/custom id columns, mixed-case tables
- Safe startup and DDL coordination with advisory locks
- Bounded queue, worker pool, event coalescing, retries, timeouts
- Metrics, health checks, CLI, ASGI integration, Alembic helpers and test utilities

## Requirements

- Python 3.12+
- PostgreSQL 10+
- `asyncpg` (`pip install sqlalchemy-events-lib[postgres]`)
- `alembic` only for the migration helpers

## Installation

```bash
pip install sqlalchemy-events-lib[postgres]
```

## Quick start

### Models

```python
import uuid

from sqlalchemy import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from sqlalchemy_events import SaEvent, with_events


class Base(DeclarativeBase):
    id: Mapped[uuid.UUID] = mapped_column(
        UUID, nullable=False, primary_key=True, default=uuid.uuid4
    )


@with_events([SaEvent.INSERT, SaEvent.UPDATE, SaEvent.DELETE])
class UserModel(Base):
    __tablename__ = 'users'

    name: Mapped[str] = mapped_column()
```

### Handlers

Handlers can be module-level functions, staticmethods or classmethods.
Only the optional `rows` parameter is supported; all other parameters must
have defaults.

```python
from sqlalchemy_events import sa_insert_handler
from models import UserModel


@sa_insert_handler(UserModel)
async def handle_user_insert(rows: list[str]) -> None:
    print('Inserted rows:', rows)
```

`rows` contains the affected primary key values (or `__events_id__` columns).
For composite keys each item is a list, for example `[[1, 'a'], [2, 'b']]`.

Sync handlers are supported and run in a thread pool by default. Set
`sync_executor='process'` to use a process pool.

### Handlers in classes

Both decorator orders are supported.

```python
from sqlalchemy_events import (
    sa_delete_handler,
    sa_insert_handler,
    sa_update_handler,
)


class UserEvents:
    @sa_insert_handler(UserModel)
    @staticmethod
    async def on_insert(rows) -> None:
        ...

    @sa_update_handler(UserModel)
    @classmethod
    async def on_update(cls, rows) -> None:
        ...

    @staticmethod
    @sa_delete_handler(UserModel)
    async def on_delete(rows) -> None:
        ...
```

- `staticmethod` receives only `rows`.
- `classmethod` receives `cls` and `rows`.
- Ordinary instance methods are **not supported**: the library has no instance
  to bind to. They are rejected during startup validation. Use a
  `staticmethod`, `classmethod`, or module-level function instead.

### Start the event system

```python
import asyncio

from sqlalchemy_events import EventsConfig, SQLAlchemyEvents
from session import engine


async def main() -> None:
    events = SQLAlchemyEvents(
        engine=engine,
        autodiscover_paths=['services.handlers'],
        config=EventsConfig(),
    )

    async with events:
        await asyncio.Event().wait()


asyncio.run(main())
```

`autodiscover_paths` accepts module paths and packages. All modules with
handlers must be reachable from them. A missing or unimportable module raises
`HandlersDiscoveryError` with the module name and the original import error.

## How it works

1. `autodiscover_paths` modules are imported at startup.
2. Decorators register handlers in a process-wide registry.
3. `sync_triggers()` creates statement-level triggers and PL/pgSQL functions.
4. A trigger sends `pg_notify` with a JSON payload:
   `op`, `schema`, `table`, `count` and optional `rows`.
5. The listener receives notifications; workers execute handlers with
   timeouts, retries and coalescing.

Only models decorated with `@with_events` are instrumented.

## Delivery guarantees and multiple instances

`LISTEN/NOTIFY` is a **broadcast** mechanism:

- every running instance receives every notification and executes its own
  registered handlers;
- with several instances, the same handler runs once **per instance** — this is
  expected behavior, not deduplication;
- notifications are not persisted. Events emitted while no instance is
  listening (and notifications during a reconnect) are lost;
- order is not guaranteed across sessions/transactions.

If you need exactly-once processing, `SKIP LOCKED`-based queues, retries or
replay, implement that in your application (for example, in the handler
itself). The library intentionally stays a thin NOTIFY transport.

Handlers should therefore be idempotent when multiple instances are running.

## Configuration

`EventsConfig` intentionally has a small surface:

| Option | Default | Description |
| --- | --- | --- |
| `schema` | `''` | Default schema; `current_schema()` is used when empty |
| `sync_executor` | `'thread'` | `'thread'` or `'process'` for sync handlers |
| `auto_sync` | `True` | Create/update triggers on startup |

Queue size, worker count, handler timeout, retries, coalescing, heartbeat,
reconnect backoff, lock timeouts and shutdown timeout are fixed internally to
sensible values.

```python
from sqlalchemy_events import EventsConfig

config = EventsConfig(
    schema='public',
    sync_executor='process',
    auto_sync=False,
)
```

## Startup and advisory locks

`sync_triggers()` runs in one transaction and takes
`pg_advisory_xact_lock` keyed by the schema and an internal channel constant.
Concurrent instances serialize their DDL, so functions and triggers are never
created concurrently. The lock is released automatically on commit, rollback
or connection loss.

- Functions carry a content marker in `COMMENT ON FUNCTION`; triggers are
  compared by function name and arguments. Repeated starts are idempotent.
- DDL has a lock timeout and fails fast if a long transaction blocks startup,
  instead of applying half of the changes.
- Crash or connection loss releases the advisory lock.

## Integrating into an application

### Project layout

A minimal integration keeps the engine, models, handlers and the event system
in separate modules:

```text
app/
  db.py          # AsyncEngine and session factory
  models.py      # SQLAlchemy models
  handlers.py    # @sa_* handlers (autodiscovered)
  events.py      # one SQLAlchemyEvents instance per process
main.py          # FastAPI / asyncio / WSGI entry point
```

`app/db.py`:

```python
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from app.config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False)
```

`app/events.py`:

```python
from sqlalchemy_events import EventsConfig, SQLAlchemyEvents

from app.db import engine

events = SQLAlchemyEvents(
    engine=engine,
    autodiscover_paths=['app.handlers'],
    config=EventsConfig(),
)
```

Create **one** `SQLAlchemyEvents` per process, at module level. Do not create
it per request. `autodiscover_paths` must be importable by that process; the
modules are imported at startup so the decorators register handlers.

### FastAPI

```python
from fastapi import FastAPI
from sqlalchemy_events import (
    EventsConfig,
    SQLAlchemyEvents,
    SQLAlchemyEventsMiddleware,
)

from app.db import engine

# In a real project this instance lives in app/events.py.
events = SQLAlchemyEvents(
    engine=engine,
    autodiscover_paths=['app.handlers'],
    config=EventsConfig(),
)

app = FastAPI()
app.add_middleware(SQLAlchemyEventsMiddleware, events=events)
```

The middleware starts `events` on ASGI `lifespan.startup`, forwards lifespan
messages to the application and stops `events` on `lifespan.shutdown`.
It does not dispose the engine; dispose it in your own shutdown hook or
lifespan if needed.

Health and metrics endpoints:

```python
from fastapi import Response


@app.get('/health')
async def health() -> dict[str, bool]:
    state = await events.health()
    return {'healthy': state.healthy}


@app.get('/metrics/events')
async def metrics() -> Response:
    return Response(
        events.prometheus_metrics(),
        media_type='text/plain',
    )
```

### FastAPI: multiple workers

```bash
uvicorn app.main:app --workers 4
```

Each worker is a separate process with its own listener and handler registry.
PostgreSQL broadcasts every notification to every listener, so the same
handler runs four times. This is expected `LISTEN/NOTIFY` behavior.

- Make handlers idempotent, or
- run a single worker, or
- implement application-level deduplication/queueing (for example, a
  `SKIP LOCKED` table processed by one worker).

The library deliberately does not deduplicate across processes.

### Regular asyncio application

```python
import asyncio

from sqlalchemy_events import EventsConfig, SQLAlchemyEvents

from app.db import engine

# In a real project this instance lives in app/events.py.
events = SQLAlchemyEvents(
    engine=engine,
    autodiscover_paths=['app.handlers'],
    config=EventsConfig(),
)


async def run_app() -> None:
    # aiohttp, aiogram, a custom server, background tasks, etc.
    await asyncio.Event().wait()


async def main() -> None:
    try:
        async with events:
            await run_app()
    finally:
        await engine.dispose()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
```

If your framework has explicit startup/shutdown hooks:

```python
await events.start()
try:
    await run_app()
finally:
    await events.stop()
```

`async with events` is a shortcut for the same `start()` / `stop()` pair.

### Sync or WSGI application

`LISTEN/NOTIFY` requires an async engine, so a pure sync WSGI app cannot run the
listener on its request thread. Run the event system in a **separate worker
process**:

`app/events_worker.py`:

```python
import asyncio

from app.db import engine
from app.events import events


async def main() -> None:
    try:
        async with events:
            await asyncio.Event().wait()
    finally:
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
```

```bash
python -m app.events_worker
```

The worker can use its own `AsyncEngine` pointing to the same database. Triggers
live in PostgreSQL, so they fire for changes made by the WSGI app, by the event
worker or by any other service. The event worker only needs to be able to
import the handler modules.

If you run several copies of this worker, every copy executes the handlers —
same broadcast semantics as above.

### Pre-creating triggers in deployment

To keep DDL out of web workers, create triggers once during deploy:

```bash
sa-events sync --url "$DATABASE_URL" --paths app.handlers
```

Then start the application with synchronization disabled:

```python
events = SQLAlchemyEvents(
    engine=engine,
    autodiscover_paths=['app.handlers'],
    config=EventsConfig(auto_sync=False),
)
```

Triggers and functions live in the database and survive restarts. The CLI and
the application use the same advisory lock, so concurrent deployments are safe.

### Handler dependencies

Handlers can use the same session factory as the application:

```python
from sqlalchemy_events import sa_insert_handler

from app.db import session_factory
from app.models import UserModel


@sa_insert_handler(UserModel)
async def on_user_created(rows: list[int]) -> None:
    async with session_factory() as session:
        ...
```

Keep handlers short. A slow handler holds a worker but does not block the
PostgreSQL connection that delivers notifications. The bounded queue reports
drops if handlers cannot keep up.

### Process lifetime checklist

- One `SQLAlchemyEvents` instance per process.
- Start it before serving traffic and stop it before disposing the engine.
- Use an async engine (`asyncpg`) for the listener.
- Make handlers idempotent when running more than one process.
- Do not create `SQLAlchemyEvents` inside a request or a short-lived task.


## Health and metrics

```python
health = await events.health()

if not health.healthy:
    ...

print(events.is_connected)
print(health.details['listener'])
```

`events.metrics` contains counters such as `notifications_received`,
`events_dropped`, `handler_failures`, `events_truncated` and
`listener_disconnects`. `events.prometheus_metrics()` returns Prometheus text.

## CLI

```bash
sa-events sync  --url "$DATABASE_URL" --paths app.handlers --dry-run
sa-events sync  --url "$DATABASE_URL" --paths app.handlers
sa-events list  --url "$DATABASE_URL"
sa-events drop  --url "$DATABASE_URL" --paths app.handlers
```

`drop` without `--paths` removes **all managed triggers and functions** in the
database. Use `--dry-run` to print SQL without executing it.

## Alembic

```python
from sqlalchemy_events.alembic import disable_events, enable_events


def upgrade() -> None:
    enable_events(UserModel)


def downgrade() -> None:
    disable_events(UserModel)
```

The helpers take the same advisory lock as the application and execute their
DDL inside the migration transaction.

## Running tests

```bash
uv sync --dev
export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/test_db"
uv run pytest -q --cov=sqlalchemy_events --cov-report=term-missing src/tests
```

The test suite recreates the `public` schema, so point `DATABASE_URL` only at a
disposable test database.

## License

MIT
