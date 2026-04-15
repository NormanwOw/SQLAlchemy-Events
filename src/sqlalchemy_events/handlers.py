from typing import Callable, Type

from sqlalchemy.orm import DeclarativeBase

from .types import SaEvent
from .registry import get_event_handlers
from .types import Handler


def __inner(func: Callable, sa_event: SaEvent, model: Type[DeclarativeBase]):
    if not issubclass(model, DeclarativeBase):
        raise RuntimeError('Model must inherit from DeclarativeBase')

    if '.' in func.__qualname__ and not isinstance(func, staticmethod):
        raise RuntimeError(f'Handler must be a regular function or staticmethod, not method or classmethod of '
                           f'{func.__qualname__.split('.')[0]}')

    if isinstance(func, staticmethod):
        func = func.__func__

    event_handlers = get_event_handlers()
    trig_name = f'sa_{model.__tablename__}_{sa_event.lower()}_notify'
    handlers: list[Handler] | None = event_handlers.get(trig_name)

    obj_handler = Handler(func=func, args={'model': model})
    if handlers and obj_handler not in handlers:
        handlers.append(obj_handler)
    else:
        event_handlers[trig_name] = [obj_handler]


def sa_insert_handler(model: Type[DeclarativeBase]):
    def decorator(func: Callable):
        __inner(func, SaEvent.INSERT, model)
        return func
    return decorator


def sa_update_handler(model: Type[DeclarativeBase]):
    def decorator(func: Callable):
        __inner(func, SaEvent.UPDATE, model)
        return func
    return decorator


def sa_delete_handler(model: Type[DeclarativeBase]):
    def decorator(func: Callable):
        __inner(func, SaEvent.DELETE, model)
        return func
    return decorator