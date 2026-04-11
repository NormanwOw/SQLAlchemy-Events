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

            self.__handlers.setdefault(trig_name, []).append(func)

            return func

        return decorator

    def on_insert(self, model: Type[DeclarativeBase]):
        return self.__sa_event_handler(model, SaEvent.INSERT)

    def on_update(self, model: Type[DeclarativeBase]):
        return self.__sa_event_handler(model, SaEvent.UPDATE)

    def on_delete(self, model: Type[DeclarativeBase]):
        return self.__sa_event_handler(model, SaEvent.DELETE)


sa_event_handler = SaEventHandler()