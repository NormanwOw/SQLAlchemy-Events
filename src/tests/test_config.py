from __future__ import annotations

import pytest

from sqlalchemy_events.config import EventsConfig
from sqlalchemy_events.exceptions import ConfigurationError
from sqlalchemy_events.types import SyncExecutorKind


def test_defaults() -> None:
    config = EventsConfig()

    assert config.schema == ''
    assert config.sync_executor is SyncExecutorKind.THREAD
    assert config.auto_sync is True


def test_string_values_are_coerced() -> None:
    config = EventsConfig(sync_executor='process')

    assert config.sync_executor is SyncExecutorKind.PROCESS


def test_custom_schema() -> None:
    assert EventsConfig(schema='billing').schema == 'billing'


@pytest.mark.parametrize(
    'schema',
    ['x' * 64, 'bad\x00schema'],
)
def test_invalid_schema(schema: str) -> None:
    with pytest.raises(ConfigurationError):
        EventsConfig(schema=schema)
