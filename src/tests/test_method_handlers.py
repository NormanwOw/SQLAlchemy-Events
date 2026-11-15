from __future__ import annotations

import pytest

from sqlalchemy_events import SaEvent
from sqlalchemy_events.exceptions import EventHandlerConfigurationError
from sqlalchemy_events.execution import build_call_plan
from sqlalchemy_events.handlers import (
    sa_delete_handler,
    sa_insert_handler,
    sa_update_handler,
)
from sqlalchemy_events.testing import emit
from sqlalchemy_events.types import Handler
from tests.test_models import MethodModel

CALLS: list[tuple[str, object, object]] = []


class InnerDecorators:
    @sa_insert_handler(MethodModel)
    @staticmethod
    async def static_handler(rows) -> None:
        CALLS.append(('inner_static', None, rows))

    @sa_update_handler(MethodModel)
    @classmethod
    async def class_handler(cls, rows) -> None:
        CALLS.append(('inner_class', cls, rows))


class OuterDecorators:
    @staticmethod
    @sa_delete_handler(MethodModel)
    async def static_handler(rows) -> None:
        CALLS.append(('outer_static', None, rows))

    @classmethod
    @sa_update_handler(MethodModel)
    async def class_handler(cls, rows) -> None:
        CALLS.append(('outer_class', cls, rows))


class InstanceHandler:
    def handler(self, rows) -> None:
        pass


class CustomNameClassmethod:
    @classmethod
    def handler(klass, rows, /) -> None:
        pass


@pytest.fixture(autouse=True)
def clear_calls():
    CALLS.clear()
    yield
    CALLS.clear()


def test_classmethod_handlers_are_registered() -> None:
    handlers = [
        handler
        for handler in _method_handlers()
        if handler.event in (SaEvent.INSERT, SaEvent.UPDATE, SaEvent.DELETE)
    ]

    assert len(handlers) == 4


def _method_handlers() -> list[Handler]:
    from sqlalchemy_events.registry import get_event_handlers

    return [handler for handler in get_event_handlers().all() if handler.table == 'method_models']


def test_method_handlers_plans() -> None:
    from sqlalchemy_events.registry import get_event_handlers

    handlers = {
        (handler.func.__qualname__, handler.event): handler
        for handler in get_event_handlers().all()
        if handler.table == 'method_models'
    }

    inner_static = handlers[('InnerDecorators.static_handler', SaEvent.INSERT)]
    inner_class = handlers[('InnerDecorators.class_handler', SaEvent.UPDATE)]
    outer_static = handlers[('OuterDecorators.static_handler', SaEvent.DELETE)]
    outer_class = handlers[('OuterDecorators.class_handler', SaEvent.UPDATE)]

    static_plan = build_call_plan(inner_static)
    class_plan = build_call_plan(inner_class)
    outer_static_plan = build_call_plan(outer_static)
    outer_class_plan = build_call_plan(outer_class)

    assert static_plan.owner is None
    assert class_plan.owner is InnerDecorators
    assert outer_static_plan.owner is None
    assert outer_class_plan.owner is OuterDecorators


async def test_staticmethod_handler_runs() -> None:
    await emit(MethodModel, SaEvent.INSERT, rows=[1])

    assert CALLS == [('inner_static', None, [1])]


async def test_classmethod_handlers_run() -> None:
    await emit(MethodModel, SaEvent.UPDATE, rows=[2])

    assert {call[0] for call in CALLS} == {'inner_class', 'outer_class'}
    assert all(call[1] is not None for call in CALLS)


async def test_outer_staticmethod_handler_runs() -> None:
    await emit(MethodModel, SaEvent.DELETE, rows=[3])

    assert CALLS == [('outer_static', None, [3])]


def test_instance_method_is_rejected() -> None:
    handler = Handler(
        func=InstanceHandler.handler,
        model=MethodModel,
        event=SaEvent.INSERT,
    )

    with pytest.raises(EventHandlerConfigurationError):
        build_call_plan(handler)


def test_classmethod_with_custom_first_parameter() -> None:
    handler = Handler(
        func=CustomNameClassmethod.__dict__['handler'].__func__,
        model=MethodModel,
        event=SaEvent.INSERT,
    )

    plan = build_call_plan(handler)

    assert plan.owner is CustomNameClassmethod
    assert plan.rows_mode == 'positional'
    assert plan.rows_position == 1
