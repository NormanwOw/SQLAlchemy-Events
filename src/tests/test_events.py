from __future__ import annotations

from sqlalchemy_events.events import SaEvents
from sqlalchemy_events.types import SaEvent


def test_events_container() -> None:
    events = SaEvents([SaEvent.INSERT, SaEvent.UPDATE])

    assert len(events) == 2
    assert list(events) == [SaEvent.INSERT, SaEvent.UPDATE]
    assert events.INSERT is SaEvent.INSERT
    assert events.UPDATE is SaEvent.UPDATE
