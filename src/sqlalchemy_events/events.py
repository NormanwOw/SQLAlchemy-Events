from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .dispatchers import BaseDispatcher, NotifyDispatcher
from .init_triggers_strategies.base import InitTriggersStrategy
from .init_triggers_strategies.postgres_init_triggers import (
    PostgresInitTriggers,
)
from .types import Dialect, SaEvent


class SaEvents:
    def __init__(self, events: list[SaEvent]):
        self.events = events
        for event in events:
            setattr(self, event.name, event)

    def __iter__(self):
        return iter(self.events)

    def __len__(self):
        return len(self.events)


def _postgres_dispatcher(**kwargs: Any) -> BaseDispatcher:
    return NotifyDispatcher(**kwargs)


@dataclass(frozen=True)
class SaEventStrategy:
    init_triggers: InitTriggersStrategy
    dispatcher_factory: Callable[..., BaseDispatcher]


sa_events_strategy: dict[Dialect, SaEventStrategy] = {
    Dialect.POSTGRESQL: SaEventStrategy(
        init_triggers=PostgresInitTriggers(),
        dispatcher_factory=_postgres_dispatcher,
    )
}
