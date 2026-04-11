from typing import Type

from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase

from ..enums import SaEvent
from .base import InitTriggersStrategy


class PostgresInitTriggers(InitTriggersStrategy):

    async def __call__(self, model_list: list[Type[DeclarativeBase]], conn, logger):
        await conn.execute(
            text("""
                 CREATE OR REPLACE FUNCTION sqlalchemy_events() 
                 RETURNS trigger AS $$ 
                 BEGIN PERFORM pg_notify(
                    'sqlalchemy_events', json_build_object(
                    'table', TG_TABLE_NAME, 'event', TG_OP, 'trigger', TG_NAME
                 )::text); 
                 RETURN NEW; END; $$ LANGUAGE plpgsql;
                 """)
        )

        for cls in model_list:
            events = getattr(cls, '__events__', None)

            if not events or not all(isinstance(e, SaEvent) for e in events):
                continue

            table_name = cls.__tablename__

            existing_triggers = set()

            table_exists = await conn.scalar(
                text("""
                     SELECT EXISTS (SELECT 1
                                    FROM pg_class
                                    WHERE relname = :tbl
                                      AND relkind = 'r')
                     """),
                {'tbl': table_name},
            )
            if not table_exists:
                if logger:
                    logger.warning(f"[SQLAlchemyEvents] {cls.__name__} has "
                                   f"{'event' if len(events) == 1 else 'events'} {', '.join(events)}, "
                                   f"but relation '{table_name}' does not exist")
                continue

            if table_exists:
                tg_sql = text("""
                              SELECT tgname
                              FROM pg_trigger
                              WHERE tgrelid = to_regclass(:tbl)
                                AND NOT tgisinternal
                              """)

                for row in await conn.execute(tg_sql, {'tbl': table_name}):
                    existing_triggers.add(row[0])

            added_events = []
            for event in sorted(events):
                l_event = event.lower()
                trig_name = f'sa_{table_name}_{l_event}_notify'

                if trig_name in existing_triggers:
                    continue

                added_events.append(event)
                await conn.execute(
                    text(f"DROP TRIGGER IF EXISTS {trig_name} ON {table_name};")
                )

                await conn.execute(
                    text(f"""
                         CREATE TRIGGER {trig_name}
                         AFTER {event} ON {table_name}
                         FOR EACH ROW EXECUTE FUNCTION sqlalchemy_events();
                         """)
                )

            if added_events and logger:
                logger.info(
                    f"Detected added {'event' if len(added_events) == 1 else 'events'} "
                    f"{', '.join(added_events)} for table '{table_name}'"
                )

        await conn.commit()