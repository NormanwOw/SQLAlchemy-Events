from __future__ import annotations

import asyncio
import os

from sqlalchemy import text

from sqlalchemy_events.cli import main

DATABASE_URL = os.environ.get('DATABASE_URL', '')


async def run_cli(args: list[str]) -> int:
    return await asyncio.to_thread(main, args)


async def trigger_count(engine) -> int:
    async with engine.connect() as conn:
        return int(
            await conn.scalar(
                text(
                    'SELECT count(*) FROM pg_trigger t '
                    'JOIN pg_class c ON c.oid = t.tgrelid '
                    'JOIN pg_namespace n ON n.oid = c.relnamespace '
                    'WHERE NOT t.tgisinternal '
                    "AND n.nspname = 'public' AND c.relname = 'users'"
                )
            )
            or 0
        )


async def test_cli_sync_dry_run(
    engine,
    db: None,
    capsys,
) -> None:
    exit_code = await run_cli(
        [
            'sync',
            '--url',
            DATABASE_URL,
            '--paths',
            'tests.handlers',
            '--dry-run',
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert 'CREATE TRIGGER' in output
    assert await trigger_count(engine) == 0


async def test_cli_sync_list_drop(
    engine,
    db: None,
    capsys,
) -> None:
    exit_code = await run_cli(
        [
            'sync',
            '--url',
            DATABASE_URL,
            '--paths',
            'tests.handlers',
        ]
    )
    assert exit_code == 0
    assert await trigger_count(engine) == 3

    exit_code = await run_cli(
        [
            'list',
            '--url',
            DATABASE_URL,
            '--paths',
            'tests.handlers',
        ]
    )
    assert exit_code == 0
    listed = capsys.readouterr().out
    assert 'sa_users_insert_notify' in listed
    assert 'public.users' in listed

    exit_code = await run_cli(
        [
            'drop',
            '--url',
            DATABASE_URL,
            '--paths',
            'tests.handlers',
            '--dry-run',
        ]
    )
    assert exit_code == 0
    dry_run_output = capsys.readouterr().out
    assert 'DROP TRIGGER' in dry_run_output
    assert await trigger_count(engine) == 3

    exit_code = await run_cli(
        [
            'drop',
            '--url',
            DATABASE_URL,
            '--paths',
            'tests.handlers',
        ]
    )
    assert exit_code == 0
    assert await trigger_count(engine) == 0


def test_cli_requires_database_url(monkeypatch, capsys) -> None:
    monkeypatch.delenv('DATABASE_URL', raising=False)

    exit_code = main(['sync', '--paths', 'tests.handlers'])

    assert exit_code == 2
    assert 'database URL is required' in capsys.readouterr().err


def test_cli_sync_requires_paths(monkeypatch) -> None:
    monkeypatch.setenv('DATABASE_URL', DATABASE_URL)
    exit_code = main(['sync'])

    assert exit_code == 2


def test_cli_unknown_command(capsys) -> None:
    try:
        main(['unknown'])
    except SystemExit as ex:
        assert ex.code == 2
    else:
        raise AssertionError('argparse should exit')
