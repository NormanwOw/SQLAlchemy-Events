from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import DeclarativeBase

from .exceptions import EventHandlerConfigurationError
from .registry import get_event_handlers
from .types import Handler, SaEvent


def _validate_model(model: type[DeclarativeBase]) -> None:
    if not isinstance(model, type) or not issubclass(model, DeclarativeBase):
        raise EventHandlerConfigurationError('Model must inherit from DeclarativeBase')

    if not getattr(model, '__tablename__', None):
        raise EventHandlerConfigurationError(f'Model {model.__name__} has no __tablename__')


def _validate_func(func: Callable[..., Any]) -> Callable[..., Any]:
    if isinstance(func, (staticmethod, classmethod)):
        func = func.__func__
    elif inspect.ismethod(func):
        func = func.__func__

    qualname = getattr(func, '__qualname__', '')

    if '<locals>' in qualname:
        raise EventHandlerConfigurationError(
            f'Handler {qualname!r} must be a module-level function or a '
            'classmethod/staticmethod, not a nested function'
        )

    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError) as ex:
        raise EventHandlerConfigurationError(f'Cannot inspect handler {qualname!r}: {ex}') from ex

    is_method = '.' in qualname

    if not is_method:
        for parameter in signature.parameters.values():
            if parameter.name == 'rows':
                continue
            if parameter.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            if parameter.default is inspect.Parameter.empty:
                raise EventHandlerConfigurationError(
                    f'Handler {qualname!r} has required parameter '
                    f"{parameter.name!r}; only the optional 'rows' parameter "
                    'is supported'
                )

    return func


def _register(
    func: Callable[..., Any],
    sa_event: SaEvent,
    model: type[DeclarativeBase],
) -> None:
    _validate_model(model)
    func = _validate_func(func)

    handler = Handler(func=func, model=model, event=sa_event)
    get_event_handlers().register(handler)


def sa_insert_handler(model: type[DeclarativeBase]):
    def decorator(func: Callable[..., Any]):
        _register(func, SaEvent.INSERT, model)
        return func

    return decorator


def sa_update_handler(model: type[DeclarativeBase]):
    def decorator(func: Callable[..., Any]):
        _register(func, SaEvent.UPDATE, model)
        return func

    return decorator


def sa_delete_handler(model: type[DeclarativeBase]):
    def decorator(func: Callable[..., Any]):
        _register(func, SaEvent.DELETE, model)
        return func

    return decorator
