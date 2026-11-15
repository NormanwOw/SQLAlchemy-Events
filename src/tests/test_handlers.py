from __future__ import annotations

import pytest

from sqlalchemy_events.exceptions import EventHandlerConfigurationError
from sqlalchemy_events.execution import build_call_plan
from sqlalchemy_events.handlers import (
    sa_insert_handler,
)
from sqlalchemy_events.registry import HandlerRegistry
from sqlalchemy_events.types import SaEvent
from tests.test_models import PlainModel, UserModel


@pytest.fixture
def registry(monkeypatch) -> HandlerRegistry:
    registry = HandlerRegistry()

    monkeypatch.setattr(
        'sqlalchemy_events.handlers.get_event_handlers',
        lambda: registry,
    )

    return registry


def test_invalid_model(registry: HandlerRegistry) -> None:
    with pytest.raises(EventHandlerConfigurationError):
        sa_insert_handler(object)(lambda: None)


def test_missing_tablename(registry: HandlerRegistry) -> None:
    class NoTable(UserModel.__bases__[0]):
        __abstract__ = True

    with pytest.raises(EventHandlerConfigurationError):
        sa_insert_handler(NoTable)(lambda: None)


def test_instance_method_is_rejected_at_plan_time(
    registry: HandlerRegistry,
) -> None:
    sa_insert_handler(UserModel)(InstanceService().method)

    handler = registry.get_handlers(None, 'users', SaEvent.INSERT)[0]

    with pytest.raises(EventHandlerConfigurationError):
        build_call_plan(handler)


def test_classmethod_is_supported(registry: HandlerRegistry) -> None:
    sa_insert_handler(UserModel)(ClassmethodService.handler)

    handler = registry.get_handlers(None, 'users', SaEvent.INSERT)[0]
    plan = build_call_plan(handler)

    assert plan.owner is ClassmethodService
    assert plan.rows_mode == 'keyword'


def test_nested_function_is_rejected(registry: HandlerRegistry) -> None:
    def outer():
        def inner() -> None:
            pass

        return inner

    with pytest.raises(EventHandlerConfigurationError):
        sa_insert_handler(UserModel)(outer())


def test_required_parameter_is_rejected(registry: HandlerRegistry) -> None:
    with pytest.raises(EventHandlerConfigurationError):
        sa_insert_handler(UserModel)(invalid_required_handler)


def static_handler(rows) -> None:
    pass


async def registered_handler(rows) -> None:
    pass


async def second_registered_handler(rows) -> None:
    pass


async def plain_registered_handler() -> None:
    pass


def invalid_required_handler(value) -> None:
    pass


class InstanceService:
    def method(self) -> None:
        pass


class ClassmethodService:
    @classmethod
    async def handler(cls, rows) -> None:
        pass


def test_staticmethod_is_supported(registry: HandlerRegistry) -> None:
    sa_insert_handler(UserModel)(staticmethod(static_handler))

    handlers = registry.get_handlers(None, 'users', SaEvent.INSERT)
    assert handlers
    assert handlers[0].func is static_handler


def test_handler_is_registered(registry: HandlerRegistry) -> None:
    sa_insert_handler(UserModel)(registered_handler)

    handlers = registry.get_handlers(None, 'users', SaEvent.INSERT)

    assert len(handlers) == 1
    assert handlers[0].func is registered_handler
    assert handlers[0].event is SaEvent.INSERT
    assert handlers[0].table == 'users'


def test_two_handlers_for_same_event_are_kept(
    registry: HandlerRegistry,
) -> None:
    sa_insert_handler(UserModel)(registered_handler)
    sa_insert_handler(UserModel)(second_registered_handler)

    assert len(registry.get_handlers(None, 'users', SaEvent.INSERT)) == 2


def test_plain_model_is_allowed_by_decorator(
    registry: HandlerRegistry,
) -> None:
    sa_insert_handler(PlainModel)(plain_registered_handler)

    assert registry.get_handlers(None, 'plain', SaEvent.INSERT)
