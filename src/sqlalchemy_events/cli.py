from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from typing import cast

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .core import SQLAlchemyEvents
from .exceptions import SQLAlchemyEventsError


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        '--url',
        default=os.environ.get('DATABASE_URL'),
        help='SQLAlchemy async database URL (default: DATABASE_URL)',
    )
    common.add_argument(
        '--paths',
        default=os.environ.get('SA_EVENTS_PATHS', ''),
        help='Comma-separated module paths to autodiscover',
    )
    common.add_argument('--schema', default='')
    common.add_argument('--verbose', action='store_true')
    common.add_argument('--dry-run', action='store_true')

    parser = argparse.ArgumentParser(
        prog='sa-events',
        description='Manage sqlalchemy-events triggers.',
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    subparsers.add_parser(
        'sync',
        parents=[common],
        help='Create or update triggers and functions',
    )
    subparsers.add_parser(
        'list',
        parents=[common],
        help='List managed triggers',
    )

    subparsers.add_parser(
        'drop',
        parents=[common],
        help='Drop managed triggers and functions',
    )

    return parser


def _paths(value: str) -> list[str]:
    return [part.strip() for part in value.split(',') if part.strip()]


def _build_events(args: argparse.Namespace) -> SQLAlchemyEvents:
    engine = create_async_engine(args.url)

    return SQLAlchemyEvents(
        engine=engine,
        autodiscover_paths=_paths(args.paths),
        schema=args.schema,
        verbose=args.verbose,
        auto_sync=False,
    )


def _print_statements(statements: Sequence[str]) -> None:
    for statement in statements:
        print(statement.rstrip().rstrip(';') + ';')


async def _run(args: argparse.Namespace) -> int:
    if not args.url:
        print(
            'error: database URL is required (--url or DATABASE_URL)',
            file=sys.stderr,
        )
        return 2

    events = _build_events(args)
    engine = cast(AsyncEngine, events.engine)

    try:
        if args.command == 'sync':
            if not events.autodiscover_paths:
                print(
                    'error: --paths is required for sync',
                    file=sys.stderr,
                )
                return 2

            result = await events.sync_triggers(dry_run=args.dry_run)

            if args.dry_run:
                _print_statements(result.statements)

            print(result.summary())
            return 0

        if args.command == 'list':
            triggers = await events.list_triggers()

            for trigger in triggers:
                print(
                    f'{trigger.schema}.{trigger.table}: '
                    f'{trigger.trigger} -> {trigger.function} '
                    f'[{trigger.event}] channel={trigger.channel}'
                )

            return 0

        if args.command == 'drop':
            drop_result = await events.drop_triggers(
                all_managed=not events.autodiscover_paths,
                dry_run=args.dry_run,
            )

            if args.dry_run:
                _print_statements(drop_result.statements)

            print(drop_result.summary())
            return 0

        return 2
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        return asyncio.run(_run(args))
    except (SQLAlchemyEventsError, SQLAlchemyError, OSError) as ex:
        print(f'error: {ex}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
