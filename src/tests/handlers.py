from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from sqlalchemy_events import (
    sa_delete_handler,
    sa_insert_handler,
    sa_update_handler,
)
from tests.test_models import (
    CompositeModel,
    CustomIdModel,
    FailingModel,
    MixedCaseModel,
    SyncModel,
    UserModel,
    UuidModel,
)

CALLS: dict[str, list[list[Any]]] = defaultdict(list)


def _record(key: str, rows: list[Any]) -> None:
    CALLS[key].append(rows)


def calls(key: str) -> list[list[Any]]:
    return list(CALLS[key])


def clear_calls() -> None:
    CALLS.clear()


async def wait_for_calls(
    key: str,
    count: int = 1,
    timeout: float = 5.0,
) -> list[list[Any]]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    while len(CALLS[key]) < count:
        if loop.time() >= deadline:
            raise AssertionError(
                f'Timed out waiting for {count} call(s) of {key!r}; got {len(CALLS[key])}'
            )
        await asyncio.sleep(0.01)

    return list(CALLS[key])


@sa_insert_handler(UserModel)
async def user_insert(rows: list[Any]) -> None:
    _record('user:insert', rows)


@sa_update_handler(UserModel)
async def user_update(rows: list[Any]) -> None:
    _record('user:update', rows)


@sa_delete_handler(UserModel)
async def user_delete(rows: list[Any]) -> None:
    _record('user:delete', rows)


@sa_insert_handler(MixedCaseModel)
async def mixed_case_insert(rows: list[Any]) -> None:
    _record('mixed:insert', rows)


@sa_insert_handler(CompositeModel)
async def composite_insert(rows: list[Any]) -> None:
    _record('composite:insert', rows)


@sa_insert_handler(CustomIdModel)
async def custom_id_insert(rows: list[Any]) -> None:
    _record('custom:insert', rows)


@sa_insert_handler(UuidModel)
async def uuid_insert(rows: list[Any]) -> None:
    _record('uuid:insert', rows)


@sa_insert_handler(FailingModel)
async def failing_insert(rows: list[Any]) -> None:
    _record('failing:insert', rows)
    raise RuntimeError('handler failure')


@sa_insert_handler(SyncModel)
def sync_insert(rows: list[Any]) -> None:
    _record('sync:insert', rows)
