from __future__ import annotations

import pytest

from sqlalchemy_events import SaEvent
from sqlalchemy_events.exceptions import HandlerExecutionError
from sqlalchemy_events.registry import HandlerRegistry
from sqlalchemy_events.testing import emit
from sqlalchemy_events.types import Handler
from tests.test_models import CompositeModel, FailingModel, UserModel


async def test_emit_calls_handler() -> None:
    calls: list[list[int]] = []

    async def handler(rows) -> None:
        calls.append(rows)

    registry = HandlerRegistry()
    registry.register(Handler(handler, UserModel, SaEvent.INSERT))

    report = await emit(
        UserModel,
        SaEvent.INSERT,
        rows=[1, 2],
        registry=registry,
    )

    assert report.ok
    assert report.handler_count == 1
    assert report.event.table == 'users'
    assert calls == [[1, 2]]


async def test_emit_by_table_name() -> None:
    calls: list[list[int]] = []

    async def handler(rows) -> None:
        calls.append(rows)

    registry = HandlerRegistry()
    registry.register(Handler(handler, UserModel, SaEvent.UPDATE))

    report = await emit(
        'users',
        SaEvent.UPDATE,
        rows=[3],
        registry=registry,
    )

    assert report.ok
    assert calls == [[3]]


async def test_emit_uses_model_schema_and_ids() -> None:
    async def handler(rows) -> None:
        pass

    registry = HandlerRegistry()
    registry.register(Handler(handler, CompositeModel, SaEvent.INSERT))

    report = await emit(
        CompositeModel,
        SaEvent.INSERT,
        rows=[[1, 2]],
        registry=registry,
    )

    assert report.ok
    assert report.event.schema == 'public'


async def test_emit_raises_handler_error() -> None:
    async def handler(rows) -> None:
        raise RuntimeError('failed')

    registry = HandlerRegistry()
    registry.register(Handler(handler, FailingModel, SaEvent.INSERT))

    with pytest.raises(HandlerExecutionError):
        await emit(FailingModel, SaEvent.INSERT, registry=registry)

    report = await emit(
        FailingModel,
        SaEvent.INSERT,
        registry=registry,
        raise_on_error=False,
    )

    assert not report.ok
    assert report.failures
