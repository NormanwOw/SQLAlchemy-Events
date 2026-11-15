from __future__ import annotations

from sqlalchemy_events.registry import HandlerRegistry
from sqlalchemy_events.types import Handler, SaEvent
from tests.test_models import MixedCaseModel, UserModel


def user_handler() -> None:
    pass


def second_handler() -> None:
    pass


def test_register_is_idempotent() -> None:
    registry = HandlerRegistry()
    handler = Handler(func=user_handler, model=UserModel, event=SaEvent.INSERT)

    assert registry.register(handler) is True
    assert registry.register(handler) is False
    assert len(registry) == 1


def test_second_registration_does_not_drop_handlers() -> None:
    registry = HandlerRegistry()
    first = Handler(func=user_handler, model=UserModel, event=SaEvent.INSERT)
    second = Handler(func=second_handler, model=UserModel, event=SaEvent.INSERT)

    registry.register(first)
    registry.register(second)
    registry.register(first)

    handlers = registry.get_handlers(None, 'users', SaEvent.INSERT)
    assert len(handlers) == 2


def test_lookup_by_schema_with_fallback() -> None:
    registry = HandlerRegistry()
    handler = Handler(
        func=user_handler,
        model=MixedCaseModel,
        event=SaEvent.INSERT,
    )
    registry.register(handler)

    assert registry.get_handlers(
        'public',
        'MixedCase',
        SaEvent.INSERT,
    ) == (handler,)
    assert registry.get_handlers(
        None,
        'MixedCase',
        SaEvent.INSERT,
    ) == (handler,)
    assert (
        registry.get_handlers(
            'other',
            'MixedCase',
            SaEvent.UPDATE,
        )
        == ()
    )


def test_groups_and_all() -> None:
    registry = HandlerRegistry()
    handler = Handler(func=user_handler, model=UserModel, event=SaEvent.INSERT)
    registry.register(handler)

    assert registry.all() == (handler,)
    groups = registry.groups()
    assert len(groups) == 1
    assert groups[0].table == 'users'
    assert groups[0].handlers == (handler,)


def test_clear() -> None:
    registry = HandlerRegistry()
    registry.register(Handler(func=user_handler, model=UserModel, event=SaEvent.INSERT))

    registry.clear()

    assert len(registry) == 0
    assert registry.all() == ()
