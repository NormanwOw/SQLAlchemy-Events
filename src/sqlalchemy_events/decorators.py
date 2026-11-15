from __future__ import annotations

import types
from collections.abc import Iterable
from typing import TypeVar

from sqlalchemy.orm import DeclarativeBase

from .exceptions import InvalidModelEventError
from .types import SaEvent

ModelT = TypeVar('ModelT', bound=type[DeclarativeBase])


def with_events(events: Iterable[SaEvent]):
    """Enable database CUD event tracking for a declarative model."""

    if isinstance(events, (str, bytes)):
        raise InvalidModelEventError('Events must be an iterable of SaEvent values')

    try:
        event_list = list(events)
    except TypeError as ex:
        raise InvalidModelEventError('Events must be an iterable of SaEvent values') from ex

    if not event_list:
        raise InvalidModelEventError('Events must contain at least one SaEvent value')

    for event in event_list:
        if not isinstance(event, SaEvent):
            raise InvalidModelEventError(f'Event must be a SaEvent instance, not {type(event)}')

    def wrapper(cls: ModelT) -> ModelT:
        if not isinstance(cls, type) or not issubclass(cls, DeclarativeBase):
            raise InvalidModelEventError('Model must inherit from DeclarativeBase')

        cls.__events__ = frozenset(event_list)
        cls.events = types.SimpleNamespace(**{event.name: event.value for event in event_list})
        return cls

    return wrapper
