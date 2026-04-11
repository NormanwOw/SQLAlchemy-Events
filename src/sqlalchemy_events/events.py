from dataclasses import dataclass

from .callbacks_strategies.base import SaEventsCallbacksStrategy
from .callbacks_strategies.postgres_callback import PostgresCallback
from .enums import Dialect, SaEvent
from .init_triggers_strategies.base import InitTriggersStrategy
from .init_triggers_strategies.postgres_init_triggers import PostgresInitTriggers


class SaEvents:

    def __init__(self, events: list[SaEvent]):
        self.events = events
        for e in events:
            setattr(self, e.name, e)

    def __iter__(self):
        return iter(self.__dict__.values())

    def __len__(self):
        return len(self.events)


@dataclass(frozen=True)
class SaEventStrategy:
    init_triggers: InitTriggersStrategy
    callback: SaEventsCallbacksStrategy


sa_events_strategy: dict[Dialect, SaEventStrategy] = {
    Dialect.POSTGRESQL: SaEventStrategy(
        init_triggers=PostgresInitTriggers(),
        callback=PostgresCallback()
    )
}