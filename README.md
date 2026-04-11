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


### Define event handlers using decorator `@sa_event_handler`  
services/handlers.py
```python
from models import UserModel
from sqlalchemy_events import sa_event_handler

@sa_event_handler.on_insert(UserModel)
async def handle_user_insert():
    print("User inserted!")
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
from models import Base
from session import engine


async def main():
    asyncio.create_task(
        SQLAlchemyEvents(
            base=Base,
            engine=engine,
            autodiscover_paths=['services']
        ).init()
    )
    try:
        await asyncio.sleep(9999)
    except:
        pass

if __name__ == '__main__':
    asyncio.run(main())
```

## Configuration
### SQLAlchemyEvents

The SQLAlchemyEvents class accepts the following parameters:
```python
SQLAlchemyEvents(
    base,
    engine,
    autodiscover_paths,
    logger=None
)
```

Parameters:
* **base** - SQLAlchemy declarative base class used to discover mapped models.
* **engine** - SQLAlchemy Engine or AsyncEngine instance.
* **autodiscover_paths** - List of Python module paths where event handlers are defined.
These modules are automatically imported so that decorators such as `@sa_event_handler` are executed. Example:`autodiscover_paths=["services", "app.handlers"]`

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

logger = logging.getLogger("sqlalchemy_events")
logger.setLevel(logging.INFO)

SQLAlchemyEvents(
    base=Base,
    engine=engine,
    autodiscover_paths=["services"],
    logger=logger
)
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
INSERT INTO {table} → DATABASE trigger fires →
NOTIFY → Python listener receives event →
handler is executed

## License

MIT