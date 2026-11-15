from __future__ import annotations

import asyncio
import threading

import pytest

from sqlalchemy_events.config import EventsConfig
from sqlalchemy_events.exceptions import EventHandlerConfigurationError
from sqlalchemy_events.execution import (
    HandlerExecutor,
    build_call_plan,
)
from sqlalchemy_events.metrics import Metrics
from sqlalchemy_events.types import Event, Handler, SaEvent
from tests.helpers import process_marker_handler
from tests.test_models import UserModel


def make_event(rows: list[int] | None = None) -> Event:
    actual = [1, 2] if rows is None else rows
    return Event(
        schema='public',
        table='users',
        op=SaEvent.INSERT,
        rows=tuple(actual),
        count=len(actual),
    )


def make_handler(func) -> Handler:
    return Handler(func=func, model=UserModel, event=SaEvent.INSERT)


def test_call_plan_keyword_rows() -> None:
    async def handler(rows) -> None:
        pass

    plan = build_call_plan(make_handler(handler))

    assert plan.is_async
    assert plan.needs_rows
    assert plan.rows_mode == 'keyword'
    assert plan.rows_position is None


def test_call_plan_without_rows() -> None:
    def handler() -> None:
        pass

    plan = build_call_plan(make_handler(handler))

    assert not plan.is_async
    assert not plan.needs_rows
    assert plan.rows_mode == 'none'


def test_call_plan_positional_only_rows() -> None:
    def handler(rows, /) -> None:
        pass

    plan = build_call_plan(make_handler(handler))

    assert plan.rows_mode == 'positional'
    assert plan.rows_position == 0


def test_call_plan_rejects_non_first_positional_rows() -> None:
    def handler(value, rows, /) -> None:
        pass

    with pytest.raises(EventHandlerConfigurationError):
        build_call_plan(make_handler(handler))


def test_call_plan_signature_failure() -> None:
    handler = make_handler(print)

    plan = build_call_plan(handler)

    assert plan.rows_mode == 'none'


async def test_async_handler_is_called() -> None:
    received: list[list[int]] = []

    async def handler(rows) -> None:
        received.append(rows)

    executor = HandlerExecutor(EventsConfig(), None, Metrics())

    try:
        failures = await executor.execute(
            make_event(),
            (make_handler(handler),),
        )
    finally:
        executor.shutdown()

    assert failures == []
    assert received == [[1, 2]]


async def test_sync_handler_runs_in_thread() -> None:
    threads: list[str] = []

    def handler(rows) -> None:
        threads.append(threading.current_thread().name)

    executor = HandlerExecutor(EventsConfig(), None, Metrics())

    try:
        await executor.execute(make_event(), (make_handler(handler),))
    finally:
        executor.shutdown()

    assert threads
    assert threads[0].startswith('sqlalchemy-events')


async def test_handler_without_rows_receives_nothing() -> None:
    calls: list[tuple] = []

    def handler(*args) -> None:
        calls.append(args)

    executor = HandlerExecutor(EventsConfig(), None, Metrics())

    try:
        await executor.execute(make_event(), (make_handler(handler),))
    finally:
        executor.shutdown()

    assert calls == [()]


async def test_retry_then_success(monkeypatch) -> None:
    monkeypatch.setattr('sqlalchemy_events.execution.HANDLER_RETRY_ATTEMPTS', 2)
    monkeypatch.setattr('sqlalchemy_events.execution.HANDLER_RETRY_BACKOFF', 0)

    attempts = 0

    async def handler(rows) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError('transient')

    metrics = Metrics()
    executor = HandlerExecutor(
        EventsConfig(),
        None,
        metrics,
    )

    try:
        failures = await executor.execute(
            make_event(),
            (make_handler(handler),),
        )
    finally:
        executor.shutdown()

    assert failures == []
    assert attempts == 3
    assert metrics.handler_retries == 2
    assert metrics.handler_failures == 0


async def test_failure_after_retries(monkeypatch) -> None:
    monkeypatch.setattr('sqlalchemy_events.execution.HANDLER_RETRY_ATTEMPTS', 1)
    monkeypatch.setattr('sqlalchemy_events.execution.HANDLER_RETRY_BACKOFF', 0)

    async def handler(rows) -> None:
        raise RuntimeError('boom')

    metrics = Metrics()
    executor = HandlerExecutor(
        EventsConfig(),
        None,
        metrics,
    )

    try:
        failures = await executor.execute(
            make_event(),
            (make_handler(handler),),
        )
    finally:
        executor.shutdown()

    assert len(failures) == 1
    assert isinstance(failures[0].error, RuntimeError)
    assert failures[0].attempts == 2
    assert 'users.insert' in failures[0].describe()
    assert metrics.handler_failures == 1
    assert metrics.last_error == 'boom'


async def test_handler_timeout(monkeypatch) -> None:
    monkeypatch.setattr('sqlalchemy_events.execution.HANDLER_TIMEOUT', 0.02)

    async def handler(rows) -> None:
        await asyncio.sleep(1)

    metrics = Metrics()
    executor = HandlerExecutor(
        EventsConfig(),
        None,
        metrics,
    )

    try:
        failures = await executor.execute(
            make_event(),
            (make_handler(handler),),
        )
    finally:
        executor.shutdown()

    assert len(failures) == 1
    assert isinstance(failures[0].error, TimeoutError)


async def test_process_pool_handler(tmp_path, monkeypatch) -> None:
    marker = tmp_path / 'marker.txt'
    monkeypatch.setenv('SA_EVENTS_TEST_MARKER', str(marker))

    handler = make_handler(process_marker_handler)
    executor = HandlerExecutor(
        EventsConfig(sync_executor='process'),
        None,
        Metrics(),
    )

    try:
        failures = await executor.execute(make_event(), (handler,))
    finally:
        executor.shutdown()

    assert failures == []
    assert marker.read_text() == '1,2'


async def test_empty_handlers() -> None:
    executor = HandlerExecutor(EventsConfig(), None, Metrics())

    try:
        assert await executor.execute(make_event(), ()) == []
    finally:
        executor.shutdown()
