from __future__ import annotations

import pytest
from sqlalchemy import Column, Integer
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect

from sqlalchemy_events.exceptions import UnsupportedDialectError
from sqlalchemy_events.types import Dialect
from sqlalchemy_events.utils import (
    dialect_resolver,
    model_schema,
    model_source_path,
    qualified_name,
    quote_identifier,
    stable_hash,
)
from tests.test_models import Base, UserModel


class FakeDialect:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeEngine:
    def __init__(self, name: str) -> None:
        self.dialect = FakeDialect(name)


@pytest.mark.parametrize(
    'name,expected',
    [
        ('postgresql', Dialect.POSTGRESQL),
        ('sqlite', Dialect.SQLITE),
        ('mysql', Dialect.MYSQL),
        ('mssql', Dialect.MSSQL),
        ('sqlserver', Dialect.MSSQL),
        ('oracle', Dialect.ORACLE),
    ],
)
def test_dialect_resolver(name: str, expected: Dialect) -> None:
    assert dialect_resolver(FakeEngine(name)) is expected  # type: ignore[arg-type]


def test_dialect_resolver_unsupported() -> None:
    with pytest.raises(UnsupportedDialectError):
        dialect_resolver(FakeEngine('db2'))  # type: ignore[arg-type]


def test_identifier_helpers() -> None:
    preparer = postgresql_dialect().identifier_preparer

    assert quote_identifier(preparer, 'users') == 'users'
    assert quote_identifier(preparer, 'MixedCase') == '"MixedCase"'
    assert qualified_name(preparer, 'public', 'users') == 'public.users'
    assert qualified_name(preparer, 'My Schema', 'users') == '"My Schema".users'
    assert qualified_name(preparer, None, 'users') == 'users'


def test_model_schema() -> None:
    class SchemaModel(Base):
        __tablename__ = 'schema_models'
        __table_args__ = {'schema': 'custom'}

        id = Column(Integer, primary_key=True)

    assert model_schema(SchemaModel) == 'custom'
    assert model_schema(UserModel) is None


def test_model_source_path() -> None:
    assert model_source_path(UserModel) == 'tests/test_models'


def test_stable_hash() -> None:
    assert stable_hash('a', 'b') == stable_hash('a', 'b')
    assert stable_hash('a', 'b') != stable_hash('a', 'c')
    assert len(stable_hash('a', 'b', length=4)) == 4
