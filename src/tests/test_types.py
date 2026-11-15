from __future__ import annotations

import random

import pytest
from sqlalchemy import Column, Integer

from sqlalchemy_events.dispatchers import parse_notification
from sqlalchemy_events.exceptions import (
    InvalidModelEventError,
    NotificationPayloadError,
)
from sqlalchemy_events.types import Event, Handler, SaEvent
from sqlalchemy_events.utils import (
    advisory_lock_key,
    backoff_delay,
    clamp_identifier,
    make_function_name,
    make_trigger_name,
    resolve_id_columns,
    sql_literal,
)
from tests.test_models import (
    Base,
    CompositeModel,
    CustomIdModel,
    MixedCaseModel,
    UserModel,
)


def test_sa_event_from_value() -> None:
    assert SaEvent.from_value('insert') is SaEvent.INSERT
    assert SaEvent.from_value('UPDATE') is SaEvent.UPDATE

    with pytest.raises(ValueError):
        SaEvent.from_value('upsert')


def test_event_merge() -> None:
    first = Event(
        schema='public',
        table='users',
        op=SaEvent.INSERT,
        rows=(1, 2),
        count=2,
    )
    second = Event(
        schema='public',
        table='users',
        op=SaEvent.INSERT,
        rows=(3,),
        count=1,
        truncated=True,
    )

    merged = first.merge(second)

    assert merged.rows == (1, 2, 3)
    assert merged.count == 3
    assert merged.truncated


def test_event_merge_with_missing_rows() -> None:
    first = Event('public', 'users', SaEvent.INSERT, rows=None, count=1)
    second = Event('public', 'users', SaEvent.INSERT, rows=(1,), count=1)

    assert first.merge(second).rows == (1,)
    assert first.merge(first).rows is None


def test_event_merge_different_keys() -> None:
    first = Event('public', 'users', SaEvent.INSERT)
    second = Event('public', 'users', SaEvent.UPDATE)

    with pytest.raises(ValueError):
        first.merge(second)

    with pytest.raises(ValueError):
        Event.merge_all([])


def test_event_merge_all() -> None:
    events = [
        Event('public', 'users', SaEvent.INSERT, rows=(1,), count=1),
        Event('public', 'users', SaEvent.INSERT, rows=(2,), count=1),
    ]

    merged = Event.merge_all(events)

    assert merged.rows == (1, 2)
    assert merged.count == 2


def test_handler_equality() -> None:
    def first() -> None:
        pass

    def second() -> None:
        pass

    handler = Handler(first, UserModel, SaEvent.INSERT)

    assert handler == Handler(first, UserModel, SaEvent.INSERT)
    assert handler != Handler(first, MixedCaseModel, SaEvent.INSERT)
    assert handler != Handler(second, UserModel, SaEvent.INSERT)
    assert handler != Handler(first, UserModel, SaEvent.UPDATE)
    assert handler != object()
    assert hash(handler) == hash(Handler(first, UserModel, SaEvent.INSERT))
    assert 'first' in handler.identity


def test_parse_v2_payload() -> None:
    event = parse_notification(
        '{"v": 2, "op": "insert", "schema": "public", "table": "users", "count": 2, "rows": [1, 2]}'
    )

    assert event.schema == 'public'
    assert event.table == 'users'
    assert event.op is SaEvent.INSERT
    assert event.rows == (1, 2)
    assert event.count == 2
    assert not event.truncated


def test_parse_v1_payload_uses_default_schema() -> None:
    event = parse_notification(
        '{"op": "update", "table": "users", "rows": [5]}',
        default_schema='app',
    )

    assert event.schema == 'app'
    assert event.count == 1


def test_parse_truncated_payload() -> None:
    event = parse_notification(
        '{"v": 2, "op": "insert", "table": "users", "count": 500, "rows": null, "truncated": true}'
    )

    assert event.rows is None
    assert event.count == 500
    assert event.truncated


@pytest.mark.parametrize(
    'payload',
    [
        '',
        'not json',
        '[]',
        '{"v": 2, "op": "insert"}',
        '{"v": 99, "op": "insert", "table": "users"}',
        '{"v": 2, "op": "upsert", "table": "users"}',
        '{"v": 2, "op": "insert", "table": "users", "rows": {}}',
        ('{"v": 2, "op": "insert", "table": "users", "count": "many"}'),
    ],
)
def test_parse_invalid_payload(payload: str) -> None:
    with pytest.raises(NotificationPayloadError):
        parse_notification(payload)


def test_identifier_limits() -> None:
    long_name = 'a' * 100
    clamped = clamp_identifier(long_name)

    assert len(clamped.encode()) <= 63
    assert clamp_identifier('short') == 'short'
    assert clamp_identifier(long_name) == clamped
    assert len(make_trigger_name(long_name, SaEvent.INSERT).encode()) <= 63


def test_function_names() -> None:
    notify = make_function_name(
        SaEvent.INSERT,
        needs_rows=False,
    )
    notify_rows = make_function_name(
        SaEvent.UPDATE,
        needs_rows=True,
    )

    assert notify == 'sqlalchemy_events_notify_insert'
    assert notify_rows == 'sqlalchemy_events_notify_update_rows'
    assert len(notify_rows.encode()) <= 63


def test_sql_literal() -> None:
    assert sql_literal("it's") == "'it''s'"


def test_backoff_delay_is_bounded() -> None:
    random.seed(1)

    for attempt in range(5):
        delay = backoff_delay(attempt, 1.0, 4.0)
        assert 0 <= delay <= 4.0

    assert backoff_delay(10, 0, 10) == 0


def test_advisory_keys_are_signed_64_bit() -> None:
    key = advisory_lock_key('public', 'channel')

    assert -(2**63) <= key < 2**63
    assert key != advisory_lock_key('public', 'other-channel')


def test_resolve_id_columns() -> None:
    assert resolve_id_columns(UserModel) == ('id',)
    assert resolve_id_columns(CompositeModel) == ('a', 'b')
    assert resolve_id_columns(CustomIdModel) == ('external_id',)


def test_resolve_id_columns_missing() -> None:
    class BadIdsModel(Base):
        __tablename__ = 'bad_ids'
        __events_id__ = 'missing'

        id = Column(Integer, primary_key=True)

    with pytest.raises(InvalidModelEventError):
        resolve_id_columns(BadIdsModel)


def test_resolve_id_columns_invalid_type() -> None:
    class BadTypeModel(Base):
        __tablename__ = 'bad_id_type'
        __events_id__ = 42

        id = Column(Integer, primary_key=True)

    with pytest.raises(InvalidModelEventError):
        resolve_id_columns(BadTypeModel)
