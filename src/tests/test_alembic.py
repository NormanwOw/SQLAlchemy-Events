from __future__ import annotations

from typing import Any

from sqlalchemy_events.alembic import (
    _plans,
    disable_events,
    enable_events,
)
from tests.test_models import UserModel


class FakeScalarResult:
    def scalar(self) -> str:
        return 'public'


class FakeBind:
    def execute(self, statement: Any) -> FakeScalarResult:
        return FakeScalarResult()


class FakeOperations:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.bind = FakeBind()

    def get_bind(self) -> FakeBind:
        return self.bind

    def execute(self, statement: str) -> None:
        self.executed.append(statement)


def test_plans_for_model() -> None:
    plans = _plans(
        UserModel,
        schema='public',
    )

    assert len(plans) == 3
    assert {plan.event.value for plan in plans} == {
        'INSERT',
        'UPDATE',
        'DELETE',
    }


def test_enable_events_executes_statements() -> None:
    operations = FakeOperations()

    statements = enable_events(UserModel, operations=operations)

    assert statements
    assert operations.executed[0].startswith('SELECT pg_advisory_xact_lock(')
    assert operations.executed[1:] == statements
    assert any('CREATE TRIGGER' in sql for sql in statements)


def test_disable_events_executes_statements() -> None:
    operations = FakeOperations()

    statements = disable_events(UserModel, operations=operations)

    assert len(statements) == 3
    assert all('DROP TRIGGER' in sql for sql in statements)
    assert operations.executed[0].startswith('SELECT pg_advisory_xact_lock(')
    assert operations.executed[1:] == statements
