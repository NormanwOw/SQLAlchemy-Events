# SQLAlchemy Events

## About  
Event-driven extension for SQLAlchemy that enables listening to database CUD events.
This library allows you to react to database changes in real time using a clean, declarative API.
* **Currently supports PostgreSQL only**


## Installation

```bash
$ pip install sqlalchemy-events-lib
```
## Quick start


### Define models with enabled event tracking using **`@with_events`**  
models.py
```python
import uuid

from sqlalchemy import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from sqlalchemy_events import with_events, SaEvent


class Base(DeclarativeBase):
    id: Mapped[uuid.UUID] = mapped_column(
        UUID, nullable=False, primary_key=True, default=uuid.uuid4
    )

@with_events([SaEvent.INSERT, SaEvent.UPDATE, SaEvent.DELETE])
class UserModel(Base):
    __tablename__ = 'users'

    name: Mapped[str] = mapped_column()
```
___


### Define event handlers using decorators `@sa_insert_handler`, `@sa_update_handler`, `@sa_delete_handler`
services/handlers.py
```python
from models import UserModel
from sqlalchemy_events import sa_insert_handler

@sa_insert_handler(UserModel)
async def handle_user_insert():
    print('User inserted!')
```

**Optional Argument**: `rows`
Handlers can optionally accept a rows argument.

If the parameter is declared in the function signature, it will automatically receive a list of affected `row` IDs.

```python
from models import UserModel
from sqlalchemy_events import sa_insert_handler

@sa_insert_handler(UserModel)
async def handle_user_insert(rows: list[DB_ID]):
    print('Users inserted!', rows)
```
**Output:**
```
Users inserted! ['3310aa38-f555-4c05-a57f-5acae32a0a7b', '6aeba1b0-8e80-4369-8adf-cf967ab7ba7a']
```

___
 
### Configure SQLAlchemy async engine  
session.py
```python
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from config import DATABASE_URL

engine = create_async_engine(DATABASE_URL)
async_session = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

```
___
 
### Initialize event system and start listening  
main.py 
```python
import asyncio
from sqlalchemy_events import SQLAlchemyEvents
from session import engine


async def main():
    sa_events = SQLAlchemyEvents(
        engine=engine,
        autodiscover_paths=['services']
    )
    await sa_events()
    while True:
        await asyncio.sleep(9999)

if __name__ == '__main__':
    asyncio.run(main())
```

## Configuration
### SQLAlchemyEvents

The SQLAlchemyEvents class accepts the following parameters:
```python
SQLAlchemyEvents(
    engine,
    autodiscover_paths,
    logger=None,
    verbose=True
)
```

Parameters:
* **engine** - SQLAlchemy Engine or AsyncEngine instance.
* **autodiscover_paths** - List of Python module paths where event handlers are defined.
These modules are automatically imported so that decorators such as 
`@sa_insert_handler`, `@sa_update_handler`, `@sa_delete_handler` are executed.  
**Example:**  
`autodiscover_paths=["services", "app.handlers"]`

* **verbose** - Enables detailed logging output. When set to True, the library will log additional informational and warning messages to help with debugging and configuration.

### Important:

All modules containing event handlers must be imported through autodiscover
This ensures decorator registration is executed at startup

### logger (optional)
A standard Python logging.Logger instance.

If provided, the library will log internal lifecycle events such as:

* successful initialization
* listener startup
* trigger setup

**Example:**
```python
import logging
import asyncio
from sqlalchemy_events import SQLAlchemyEvents
from session import engine

logger = logging.getLogger('sqlalchemy_events')
logger.setLevel(logging.INFO)

async def main():
    sa_events = SQLAlchemyEvents(
        engine=engine,
        autodiscover_paths=['services'],
        logger=logger
    )
    await sa_events()
    while True:
        await asyncio.sleep(9999)

if __name__ == '__main__':
    asyncio.run(main())
```

## How it works
1. autodiscover_paths modules are imported at startup
2. Decorators register event handlers into a global registry
3. Triggers send events via LISTEN/NOTIFY
4. The library receives notifications and dispatches them to registered handlers

## Notes
* Handlers can be both async and regular functions
* Only models registered with @with_events will emit events
* Currently supports LISTEN/NOTIFY
* Ensure handlers are imported via autodiscover_paths, otherwise they will not be registered

## Example flow
INSERT INTO/UPDATE/DELETE FROM {table} → DATABASE trigger fires →
NOTIFY → Python listener receives event →
handler is executed

## License

MIT