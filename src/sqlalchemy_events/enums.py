from enum import StrEnum


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