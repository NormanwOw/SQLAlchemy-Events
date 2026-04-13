from .types import SaEvent


def with_events(events: list[SaEvent]):
    def wrapper(cls):
        cls.__events__ = set(events)

        class Events:
            for e in events:
                locals()[e.name] = e.value

        cls.events = Events
        return cls
    return wrapper
