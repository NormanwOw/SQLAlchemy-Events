from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Awaitable, Callable, TypeAlias, Union


class SaEvent(StrEnum):
    INSERT = 'INSERT'
    UPDATE = 'UPDATE'
    DELETE = 'DELETE'


class Dialect(StrEnum):
    POSTGRESQL = 'postgresql'
    SQLITE = 'sqlite'
    MYSQL = 'mysql'
    MSSQL = 'mssql'
    ORACLE = 'oracle'


@dataclass
class Handler:
    func: Union[Callable, Awaitable]
    args: dict[str, Any]


DB_ID: TypeAlias = str | int
