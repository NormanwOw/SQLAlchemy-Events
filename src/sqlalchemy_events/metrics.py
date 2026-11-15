from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Metrics:
    """Runtime counters exposed by :class:`SQLAlchemyEvents`."""

    notifications_received: int = 0
    notifications_invalid: int = 0
    events_dispatched: int = 0
    events_skipped: int = 0
    events_dropped: int = 0
    events_truncated: int = 0
    handler_calls: int = 0
    handler_failures: int = 0
    handler_retries: int = 0
    listener_connects: int = 0
    listener_disconnects: int = 0
    listener_failures: int = 0
    heartbeats: int = 0
    last_notification_at: datetime | None = None
    last_event_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None

    def record_error(self, error: BaseException | str) -> None:
        self.last_error = str(error)
        self.last_error_at = utcnow()

    def reset(self) -> None:
        for field_name in self.__dataclass_fields__:
            if field_name.startswith('last_'):
                setattr(self, field_name, None)
            else:
                setattr(self, field_name, 0)

    def as_dict(self) -> dict[str, object]:
        result = asdict(self)
        for key, value in result.items():
            if isinstance(value, datetime):
                result[key] = value.isoformat()
        return result

    def prometheus(self) -> str:
        lines: list[str] = []

        for name, value in sorted(self.as_dict().items()):
            if name.startswith('last_') or value is None:
                continue
            if isinstance(value, datetime):
                continue
            lines.append(f'sqlalchemy_events_{name} {value}')

        return '\n'.join(lines) + ('\n' if lines else '')


@dataclass
class Health:
    healthy: bool
    connected: bool
    queue_size: int = 0
    last_notification_at: datetime | None = None
    details: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        result = asdict(self)
        if self.last_notification_at is not None:
            result['last_notification_at'] = self.last_notification_at.isoformat()
        return result
