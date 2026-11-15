from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from types import ModuleType
from typing import cast

from .types import Handler, SaEvent

HandlerKey = tuple[str | None, str, SaEvent]


@dataclass(frozen=True)
class HandlerGroup:
    schema: str | None
    table: str
    event: SaEvent
    handlers: tuple[Handler, ...]


class HandlerRegistry:
    """Process-wide registry of event handlers.

    The registry is stored in :data:`sys.modules` so that module reloading
    does not create a second independent registry.
    """

    def __init__(self) -> None:
        self.handlers: dict[HandlerKey, list[Handler]] = {}
        self._lock = threading.RLock()

    def register(self, handler: Handler) -> bool:
        key: HandlerKey = (handler.schema, handler.table, handler.event)

        with self._lock:
            handlers = self.handlers.setdefault(key, [])
            if handler in handlers:
                return False
            handlers.append(handler)
            return True

    def get_handlers(
        self,
        schema: str | None,
        table: str,
        event: SaEvent,
    ) -> tuple[Handler, ...]:
        with self._lock:
            exact = self.handlers.get((schema, table, event))
            if exact:
                return tuple(exact)

            fallback = self.handlers.get((None, table, event))
            if fallback:
                return tuple(fallback)

            return ()

    def has_handlers(self, schema: str | None, table: str, event: SaEvent) -> bool:
        return bool(self.get_handlers(schema, table, event))

    def all(self) -> tuple[Handler, ...]:
        with self._lock:
            return tuple(handler for handlers in self.handlers.values() for handler in handlers)

    def groups(self) -> tuple[HandlerGroup, ...]:
        with self._lock:
            return tuple(
                HandlerGroup(
                    schema=schema,
                    table=table,
                    event=event,
                    handlers=tuple(handlers),
                )
                for (schema, table, event), handlers in self.handlers.items()
            )

    def clear(self) -> None:
        with self._lock:
            self.handlers.clear()

    def __len__(self) -> int:
        with self._lock:
            return sum(len(handlers) for handlers in self.handlers.values())


_GLOBAL_KEY = 'sqlalchemy_events._global_registry'

_holder = sys.modules.get(_GLOBAL_KEY)

if not isinstance(_holder, ModuleType) or not hasattr(_holder, 'registry'):
    _holder = ModuleType(_GLOBAL_KEY)
    _holder.registry = HandlerRegistry()  # type: ignore[attr-defined]
    sys.modules[_GLOBAL_KEY] = _holder

_registry: HandlerRegistry = cast(
    HandlerRegistry,
    _holder.registry,  # type: ignore[attr-defined]
)


def get_event_handlers() -> HandlerRegistry:
    return _registry


def reset_event_handlers() -> None:
    """Clear the global registry. Intended for tests."""

    _registry.clear()
