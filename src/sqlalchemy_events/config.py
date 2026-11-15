from __future__ import annotations

from dataclasses import dataclass

from .exceptions import ConfigurationError
from .types import SyncExecutorKind

MAX_IDENTIFIER_BYTES = 63


def _validate_schema(value: str) -> str:
    if value == '':
        return value

    if '\x00' in value:
        raise ConfigurationError('schema must not contain NUL bytes')

    if len(value.encode()) > MAX_IDENTIFIER_BYTES:
        raise ConfigurationError(f'schema must be at most {MAX_IDENTIFIER_BYTES} bytes')

    return value


@dataclass(frozen=True, slots=True)
class EventsConfig:
    """Configuration of the event system."""

    schema: str = ''
    sync_executor: SyncExecutorKind = SyncExecutorKind.THREAD
    auto_sync: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            'sync_executor',
            SyncExecutorKind(self.sync_executor),
        )
        _validate_schema(self.schema)
