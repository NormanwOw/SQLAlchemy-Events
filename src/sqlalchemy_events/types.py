import inspect
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
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
    full_path: str | None = None

    def __post_init__(self):
        func_path = inspect.getsourcefile(self.func) or inspect.getfile(self.func)
        func_path = Path(func_path)
        self.full_path = f'{func_path.parent.name}/{func_path.name}/{self.func.__name__}'

    def __hash__(self):
        return hash(self.full_path)

    def __eq__(self, other):
        return self.full_path == other.full_path


DB_ID: TypeAlias = str | int
