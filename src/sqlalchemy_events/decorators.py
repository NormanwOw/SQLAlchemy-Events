from .types import SaEvent


def with_events(events: list[SaEvent]):
    for e in events:
        if not isinstance(e, SaEvent):
            raise RuntimeError(f'Event must be a SaEvent instance, not {type(e)}')

    def wrapper(cls):
        cls.__events__ = set(events)

        class Events:
            for e in events:
                locals()[e.name] = e.value

        cls.events = Events
        return cls
    return wrapper
