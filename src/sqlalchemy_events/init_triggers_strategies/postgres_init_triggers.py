from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect

from ..config import EventsConfig
from ..defaults import ADVISORY_LOCK_TIMEOUT, CHANNEL, LOCK_TIMEOUT
from ..exceptions import ConfigurationError
from ..utils import (
    advisory_lock_key,
    make_function_name,
    make_trigger_name,
    model_table_name,
    quote_identifier,
    resolve_id_columns,
    sql_literal,
    stable_hash,
)
from .base import (
    DropResult,
    InitTriggersStrategy,
    ManagedTrigger,
    SyncResult,
    TriggerPlan,
    TriggerTarget,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection


_PREPARER = postgresql_dialect().identifier_preparer

_PAYLOAD_VERSION = 2
_NOTIFY_SIZE_LIMIT = 7900


def _q(identifier: str) -> str:
    return quote_identifier(_PREPARER, identifier)


def _qualified(schema: str, table: str) -> str:
    return f'{_q(schema)}.{_q(table)}'


class PostgresInitTriggers(InitTriggersStrategy):
    """PostgreSQL implementation of trigger management."""

    def build_plans(
        self,
        targets: Sequence[TriggerTarget],
        *,
        default_schema: str,
        config: EventsConfig,
    ) -> list[TriggerPlan]:
        plans: list[TriggerPlan] = []

        for target in targets:
            table = model_table_name(target.model)

            if len(table.encode()) > 63:
                raise ConfigurationError(
                    f"Table name {table!r} is longer than PostgreSQL's 63-byte identifier limit"
                )

            schema = target.model.__table__.schema or default_schema

            if len(schema.encode()) > 63:
                raise ConfigurationError(
                    f"Schema name {schema!r} is longer than PostgreSQL's 63-byte identifier limit"
                )

            id_columns = resolve_id_columns(target.model)

            function_name = make_function_name(
                target.event,
                needs_rows=target.needs_rows,
            )

            channel = CHANNEL

            plans.append(
                TriggerPlan(
                    model=target.model,
                    schema=schema,
                    table=table,
                    event=target.event,
                    needs_rows=target.needs_rows,
                    channel=channel,
                    trigger_name=make_trigger_name(table, target.event),
                    function_name=function_name,
                    id_columns=id_columns,
                )
            )

        return plans

    def _rows_select(self, plan: TriggerPlan) -> str:
        transition = plan.transition_table

        return (
            '    IF array_length(TG_ARGV, 1) = 2 THEN\n'
            '        SELECT coalesce(jsonb_agg(to_jsonb(n) -> TG_ARGV[1]), '
            "'[]'::jsonb)\n"
            '          INTO row_ids\n'
            f'          FROM {transition} AS n;\n'
            '    ELSE\n'
            "        SELECT coalesce(jsonb_agg(pk), '[]'::jsonb)\n"
            '          INTO row_ids\n'
            '          FROM (\n'
            '              SELECT (\n'
            '                  SELECT coalesce(\n'
            '                      jsonb_agg(to_jsonb(n) -> t.col '
            'ORDER BY t.ord),\n'
            "                      '[]'::jsonb\n"
            '                  )\n'
            '                  FROM unnest(TG_ARGV) WITH ORDINALITY '
            'AS t(col, ord)\n'
            '                  WHERE t.ord > 1\n'
            '              ) AS pk\n'
            f'              FROM {transition} AS n\n'
            '          ) AS s;\n'
            '    END IF;\n'
        )

    def _function_sql(self, plan: TriggerPlan) -> str:
        op = sql_literal(plan.event.value.lower())
        transition = plan.transition_table
        has_rows = plan.needs_rows

        declarations = [
            '    payload text;',
            '    row_count bigint;',
        ]
        if has_rows:
            declarations.append('    row_ids jsonb;')

        body: list[str] = [
            f'    SELECT count(*) INTO row_count FROM {transition};',
            '    IF row_count = 0 THEN',
            '        RETURN NULL;',
            '    END IF;',
        ]

        if has_rows:
            body.append(self._rows_select(plan).rstrip('\n'))

        payload_fields = [
            f"            'v', {_PAYLOAD_VERSION}",
            f"            'op', {op}",
            "            'schema', TG_TABLE_SCHEMA",
            "            'table', TG_TABLE_NAME",
            "            'count', row_count",
        ]
        if has_rows:
            payload_fields.append("            'rows', row_ids")

        body.append('    payload := jsonb_build_object(')
        body.append(',\n'.join(payload_fields))
        body.append('    )::text;')

        if has_rows:
            truncated_fields = [
                f"                'v', {_PAYLOAD_VERSION}",
                f"                'op', {op}",
                "                'schema', TG_TABLE_SCHEMA",
                "                'table', TG_TABLE_NAME",
                "                'count', row_count",
                "                'rows', NULL",
                "                'truncated', TRUE",
            ]
            body.extend(
                [
                    f'    IF octet_length(payload) > {_NOTIFY_SIZE_LIMIT} THEN',
                    '        payload := jsonb_build_object(',
                    ',\n'.join(truncated_fields),
                    '        )::text;',
                    "        RAISE WARNING 'sqlalchemy_events: NOTIFY "
                    "payload truncated for %.%: % rows',",
                    '            TG_TABLE_SCHEMA, TG_TABLE_NAME, row_count;',
                    '    END IF;',
                ]
            )

        body.append('    PERFORM pg_notify(TG_ARGV[0], payload);')
        body.append('    RETURN NULL;')

        function = _qualified(plan.schema, plan.function_name)

        return (
            f'CREATE OR REPLACE FUNCTION {function}()\n'
            'RETURNS trigger\n'
            'LANGUAGE plpgsql\n'
            'AS $sqlalchemy_events$\n'
            'DECLARE\n' + '\n'.join(declarations) + '\nBEGIN\n' + '\n'.join(body) + '\nEND;\n'
            '$sqlalchemy_events$'
        )

    def _trigger_sql(self, plan: TriggerPlan) -> str:
        args = ', '.join(sql_literal(value) for value in (plan.channel, *plan.id_columns))
        referencing = 'OLD' if plan.is_delete else 'NEW'

        return (
            f'CREATE TRIGGER {_q(plan.trigger_name)}\n'
            f'AFTER {plan.event.value} ON '
            f'{_qualified(plan.schema, plan.table)}\n'
            f'REFERENCING {referencing} TABLE AS '
            f'{plan.transition_table}\n'
            'FOR EACH STATEMENT\n'
            f'EXECUTE FUNCTION '
            f'{_qualified(plan.schema, plan.function_name)}({args})'
        )

    def _drop_trigger_sql(self, plan: TriggerPlan) -> str:
        return (
            f'DROP TRIGGER IF EXISTS {_q(plan.trigger_name)} ON '
            f'{_qualified(plan.schema, plan.table)}'
        )

    @staticmethod
    def _trigger_args(plan: TriggerPlan) -> tuple[str, ...]:
        return (plan.channel, *plan.id_columns)

    @staticmethod
    def _decode_tgargs(raw_hex: str) -> tuple[str, ...]:
        raw = bytes.fromhex(raw_hex)
        return tuple(part.decode('utf-8', 'replace') for part in raw.split(b'\x00') if part)

    async def _lock(
        self,
        conn: AsyncConnection,
        config: EventsConfig,
        schema: str,
    ) -> None:
        await conn.execute(
            text("SELECT set_config('lock_timeout', :value, true)"),
            {'value': f'{int(ADVISORY_LOCK_TIMEOUT * 1000)}ms'},
        )
        await conn.execute(
            text('SELECT pg_advisory_xact_lock(:key)'),
            {
                'key': advisory_lock_key(
                    schema,
                    CHANNEL,
                )
            },
        )
        await conn.execute(
            text("SELECT set_config('lock_timeout', :value, true)"),
            {'value': f'{int(LOCK_TIMEOUT * 1000)}ms'},
        )

    @staticmethod
    def _function_marker(sql: str) -> str:
        return 'sqlalchemy_events:' + stable_hash(sql, length=16)

    async def _existing_tables(
        self,
        conn: AsyncConnection,
        plans: Sequence[TriggerPlan],
    ) -> set[tuple[str, str]]:
        existing: set[tuple[str, str]] = set()
        seen: set[tuple[str, str]] = set()

        for plan in plans:
            key = (plan.schema, plan.table)
            if key in seen:
                continue
            seen.add(key)

            exists = await conn.scalar(
                text('SELECT to_regclass(:name) IS NOT NULL'),
                {'name': _qualified(plan.schema, plan.table)},
            )
            if exists:
                existing.add(key)

        return existing

    async def _fetch_function_comments(
        self,
        conn: AsyncConnection,
        plans: Sequence[TriggerPlan],
    ) -> dict[tuple[str, str], str | None]:
        schemas = sorted({plan.schema for plan in plans})
        names = sorted({plan.function_name for plan in plans})

        rows = await conn.execute(
            text(
                'SELECT n.nspname, p.proname, '
                "obj_description(p.oid, 'pg_proc')\n"
                'FROM pg_proc p\n'
                'JOIN pg_namespace n ON n.oid = p.pronamespace\n'
                'WHERE n.nspname = ANY(:schemas)\n'
                '  AND p.proname = ANY(:names)'
            ),
            {'schemas': schemas, 'names': names},
        )

        return {(row[0], row[1]): row[2] for row in rows}

    async def sync(
        self,
        conn: AsyncConnection,
        plans: Sequence[TriggerPlan],
        *,
        config: EventsConfig,
        dry_run: bool = False,
    ) -> SyncResult:
        result = SyncResult(dry_run=dry_run)

        if not plans:
            return result

        lock_schema = plans[0].schema

        if not dry_run:
            await self._lock(conn, config, lock_schema)

        existing_tables = await self._existing_tables(conn, plans)
        valid_plans = [plan for plan in plans if (plan.schema, plan.table) in existing_tables]

        missing = sorted(
            {
                f'{plan.schema}.{plan.table}'
                for plan in plans
                if (plan.schema, plan.table) not in existing_tables
            }
        )
        result.missing_tables = missing

        if not valid_plans:
            return result

        function_specs: dict[tuple[str, str], str] = {}

        for plan in valid_plans:
            key = (plan.schema, plan.function_name)
            if key in function_specs:
                continue
            function_specs[key] = self._function_sql(plan)

        existing_functions = await self._fetch_function_comments(
            conn,
            valid_plans,
        )

        for (schema, function_name), statement in function_specs.items():
            marker = self._function_marker(statement)

            if existing_functions.get((schema, function_name)) == marker:
                continue

            result.statements.append(statement)
            result.functions.append(f'{schema}.{function_name}')

            if dry_run:
                continue

            await conn.execute(text(statement))
            await conn.execute(
                text(
                    f'COMMENT ON FUNCTION '
                    f'{_qualified(schema, function_name)}() IS '
                    f'{sql_literal(marker)}'
                )
            )

        existing = await self._fetch_triggers(conn, valid_plans)

        for plan in valid_plans:
            expected = (
                plan.function_name,
                self._trigger_args(plan),
            )
            current = existing.get((plan.schema, plan.table, plan.trigger_name))

            if current == expected:
                continue

            if current is not None:
                drop_statement = self._drop_trigger_sql(plan)
                result.statements.append(drop_statement)
                result.replaced_triggers.append(plan.trigger_name)
                if not dry_run:
                    await conn.execute(text(drop_statement))
            else:
                result.created_triggers.append(plan.trigger_name)

            statement = self._trigger_sql(plan)
            result.statements.append(statement)
            if not dry_run:
                await conn.execute(text(statement))

        return result

    async def _fetch_triggers(
        self,
        conn: AsyncConnection,
        plans: Sequence[TriggerPlan],
    ) -> dict[tuple[str, str, str], tuple[str, tuple[str, ...]]]:
        tables_by_schema: dict[str, set[str]] = {}

        for plan in plans:
            tables_by_schema.setdefault(plan.schema, set()).add(plan.table)

        result: dict[
            tuple[str, str, str],
            tuple[str, tuple[str, ...]],
        ] = {}

        for schema, tables in tables_by_schema.items():
            rows = await conn.execute(
                text(
                    'SELECT c.relname, t.tgname, p.proname, '
                    "encode(t.tgargs, 'hex')\n"
                    'FROM pg_trigger t\n'
                    'JOIN pg_class c ON c.oid = t.tgrelid\n'
                    'JOIN pg_namespace n ON n.oid = c.relnamespace\n'
                    'JOIN pg_proc p ON p.oid = t.tgfoid\n'
                    'JOIN pg_namespace pn ON pn.oid = p.pronamespace\n'
                    'WHERE NOT t.tgisinternal\n'
                    '  AND n.nspname = :schema\n'
                    '  AND pn.nspname = n.nspname\n'
                    '  AND c.relname = ANY(:tables)'
                ),
                {'schema': schema, 'tables': sorted(tables)},
            )

            for table, trigger, function, args_hex in rows:
                result[(schema, table, trigger)] = (
                    function,
                    self._decode_tgargs(args_hex or ''),
                )

        return result

    async def drop(
        self,
        conn: AsyncConnection,
        *,
        config: EventsConfig,
        default_schema: str,
        plans: Sequence[TriggerPlan] | None = None,
        all_managed: bool = False,
        dry_run: bool = False,
    ) -> DropResult:
        result = DropResult(dry_run=dry_run)

        if not dry_run:
            await self._lock(conn, config, default_schema)

        managed = await self._list_managed_rows(
            conn,
            schema=None if all_managed else default_schema,
        )

        allowed: set[tuple[str, str]] | None = None
        if plans is not None and not all_managed:
            allowed = {(plan.schema, plan.table) for plan in plans}

        for trigger in managed:
            if (
                allowed is not None
                and (
                    trigger.schema,
                    trigger.table,
                )
                not in allowed
            ):
                continue

            statement = (
                f'DROP TRIGGER IF EXISTS {_q(trigger.trigger)} ON '
                f'{_qualified(trigger.schema, trigger.table)}'
            )
            result.statements.append(statement)
            result.triggers.append(trigger.trigger)
            if not dry_run:
                await conn.execute(text(statement))

        functions = await self._list_function_rows(
            conn,
            schema=None if all_managed else default_schema,
        )

        allowed_functions: set[tuple[str, str]] | None = None
        if plans is not None and not all_managed:
            allowed_functions = {(plan.schema, plan.function_name) for plan in plans}

        for schema, function in functions:
            if (
                allowed_functions is not None
                and (
                    schema,
                    function,
                )
                not in allowed_functions
            ):
                continue

            statement = f'DROP FUNCTION IF EXISTS {_qualified(schema, function)}()'
            result.statements.append(statement)
            result.functions.append(f'{schema}.{function}')
            if not dry_run:
                await conn.execute(text(statement))

        return result

    async def _list_managed_rows(
        self,
        conn: AsyncConnection,
        *,
        schema: str | None,
    ) -> list[ManagedTrigger]:
        schema_filter = 'AND n.nspname = :schema' if schema else ''

        rows = await conn.execute(
            text(
                'SELECT n.nspname, c.relname, t.tgname, p.proname, '
                "encode(t.tgargs, 'hex')\n"
                'FROM pg_trigger t\n'
                'JOIN pg_class c ON c.oid = t.tgrelid\n'
                'JOIN pg_namespace n ON n.oid = c.relnamespace\n'
                'JOIN pg_proc p ON p.oid = t.tgfoid\n'
                'JOIN pg_namespace pn ON pn.oid = p.pronamespace\n'
                'WHERE NOT t.tgisinternal\n'
                '  AND pn.nspname = n.nspname\n'
                "  AND p.proname LIKE 'sqlalchemy_events_notify_%'\n"
                '  AND n.nspname NOT IN '
                "('pg_catalog', 'information_schema')\n"
                f'  {schema_filter}\n'
                'ORDER BY n.nspname, c.relname, t.tgname'
            ),
            {'schema': schema} if schema else {},
        )

        result: list[ManagedTrigger] = []

        for (
            schema_name,
            table,
            trigger,
            function,
            args_hex,
        ) in rows:
            args = self._decode_tgargs(args_hex or '')
            event = next(
                (name for name in ('insert', 'update', 'delete') if name in function),
                None,
            )
            result.append(
                ManagedTrigger(
                    schema=schema_name,
                    table=table,
                    trigger=trigger,
                    function=function,
                    event=event,
                    channel=args[0] if args else None,
                )
            )

        return result

    async def _list_function_rows(
        self,
        conn: AsyncConnection,
        *,
        schema: str | None,
    ) -> list[tuple[str, str]]:
        schema_filter = 'AND n.nspname = :schema' if schema else ''

        rows = await conn.execute(
            text(
                'SELECT n.nspname, p.proname\n'
                'FROM pg_proc p\n'
                'JOIN pg_namespace n ON n.oid = p.pronamespace\n'
                "WHERE p.proname LIKE 'sqlalchemy_events_notify_%'\n"
                '  AND n.nspname NOT IN '
                "('pg_catalog', 'information_schema')\n"
                f'  {schema_filter}\n'
                'ORDER BY n.nspname, p.proname'
            ),
            {'schema': schema} if schema else {},
        )

        return [(row[0], row[1]) for row in rows]

    async def list_managed(
        self,
        conn: AsyncConnection,
        *,
        config: EventsConfig,
        default_schema: str,
    ) -> list[ManagedTrigger]:
        return await self._list_managed_rows(conn, schema=default_schema)

    def render_enable(
        self,
        plans: Sequence[TriggerPlan],
        *,
        config: EventsConfig,
    ) -> list[str]:
        if not plans:
            return []

        statements: list[str] = []

        seen_functions: set[tuple[str, str]] = set()
        for plan in plans:
            key = (plan.schema, plan.function_name)
            if key not in seen_functions:
                seen_functions.add(key)
                statements.append(self._function_sql(plan))

            statements.append(self._drop_trigger_sql(plan))
            statements.append(self._trigger_sql(plan))

        return statements

    def render_disable(
        self,
        plans: Sequence[TriggerPlan],
        *,
        config: EventsConfig,
    ) -> list[str]:
        return [self._drop_trigger_sql(plan) for plan in plans]
