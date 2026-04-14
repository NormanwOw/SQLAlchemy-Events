import inspect
from pathlib import Path
from typing import Callable, Type

from sqlalchemy.orm import DeclarativeBase

from .types import SaEvent
from .registry import get_event_handlers
from .types import Handler


def __inner(func: Callable, sa_event: SaEvent, model: Type[DeclarativeBase]):
    if not isinstance(model, type) or not issubclass(model, DeclarativeBase):
        raise RuntimeError('Model must inherit from DeclarativeBase')

    event_handlers = get_event_handlers()
    trig_name = f'sa_{model.__tablename__}_{sa_event.lower()}_notify'
    func_path = inspect.getsourcefile(func) or inspect.getfile(func)
    func_path = Path(func_path)
    func_path_name = f'{func_path.parent.name}/{func_path.name}/{func.__name__}'
    handlers: list[Handler] | None = event_handlers.get(trig_name)
    obj_handler = Handler(func=func, args={'model': model})
    if handlers:
        for handler in handlers:
            handler_path = inspect.getsourcefile(handler.func) or inspect.getfile(handler.func)
            handler_path = Path(handler_path)
            handler_path_name = f'{handler_path.parent.name}/{handler_path.name}/{handler.func.__name__}'
            if func_path_name == handler_path_name:
                continue
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