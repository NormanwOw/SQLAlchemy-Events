import inspect
from pathlib import Path
from typing import Callable, Type
from sqlalchemy.orm import DeclarativeBase

from .enums import SaEvent
from .registry import get_event_handlers


class SaEventHandler:

    def __init__(self):
        self.__handlers = get_event_handlers()

    def __sa_event_handler(self, model: Type[DeclarativeBase], event: SaEvent):
        if event not in getattr(model, '__events__', set()):
            raise ValueError(
                f'[SQLAlchemyEvents] Event {event} is not registered for model {model.__name__}'
            )

        def decorator(func: Callable):
            trig_name = f'sa_{model.__tablename__}_{event.lower()}_notify'
            func_path = inspect.getsourcefile(func) or inspect.getfile(func)
            func_path = Path(func_path)
            func_path_name = f'{func_path.parent.name}/{func_path.name}/{func.__name__}'
            funcs = self.__handlers.get(trig_name)
            if funcs:
                for handler in funcs:
                    handler_path = inspect.getsourcefile(handler) or inspect.getfile(handler)
                    handler_path = Path(handler_path)
                    handler_path_name = f'{handler_path.parent.name}/{handler_path.name}/{handler.__name__}'
                    if func_path_name == handler_path_name:
                        continue
                    funcs[trig_name].append(handler)
            else:
                self.__handlers[trig_name] = [func]

            return func

        return decorator

    def on_insert(self, model: Type[DeclarativeBase]):
        return self.__sa_event_handler(model, SaEvent.INSERT)

    def on_update(self, model: Type[DeclarativeBase]):
        return self.__sa_event_handler(model, SaEvent.UPDATE)

    def on_delete(self, model: Type[DeclarativeBase]):
        return self.__sa_event_handler(model, SaEvent.DELETE)


sa_event_handler = SaEventHandler()