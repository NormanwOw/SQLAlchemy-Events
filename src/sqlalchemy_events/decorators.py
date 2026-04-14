from sqlalchemy.orm import DeclarativeBase

from .types import SaEvent


def with_events(events: list[SaEvent]):
    if not isinstance(events, list) or not events:
        raise RuntimeError('Events must be a list with at least one SeEvent element')

    for e in events:
        if not isinstance(e, SaEvent):
            raise RuntimeError(f'Event must be a SaEvent instance, not {type(e)}')

    def wrapper(cls):
        if not issubclass(cls, DeclarativeBase):
            raise RuntimeError('Model must inherit from DeclarativeBase')
        cls.__events__ = set(events)

        class Events:
            for e in events:
                locals()[e.name] = e.value

        cls.events = Events
        return cls
    return wrapper
