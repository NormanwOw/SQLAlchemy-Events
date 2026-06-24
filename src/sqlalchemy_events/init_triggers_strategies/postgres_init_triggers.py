from typing import Type

from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase

from ..types import SaEvent
from .base import InitTriggersStrategy


class PostgresInitTriggers(InitTriggersStrategy):

    async def __call__(self, model_list: list[Type[DeclarativeBase]], conn, logger):
        result = await conn.execute(text("SELECT current_schema()"))
        schema = result.scalar()
        for sa_event in SaEvent:
            sa_event = sa_event.lower()
            await conn.execute(
                text(f"""
                        CREATE OR REPLACE FUNCTION sqlalchemy_events_notify_{sa_event}()
                        RETURNS trigger AS $$
                        DECLARE
                            payload json;
                        BEGIN
                            payload := json_build_object(
                                'op', '{sa_event}',
                                'table', TG_full_name,
                                'rows', array_agg(n.id)
                            )
                            FROM {'old_rows' if sa_event.upper() == SaEvent.DELETE else 'new_rows'} n;
                        
                            PERFORM pg_notify('sqlalchemy_events', payload::text);
                            RETURN NULL;
                        END;
                        $$ LANGUAGE plpgsql;
                     """)
            )

        for cls in model_list:
            events = getattr(cls, '__events__', None)

            if not events or not all(isinstance(e, SaEvent) for e in events):
                continue

            table_name = cls.__tablename__
            full_name = f'{schema}.{table_name}'
            existing_triggers = set()

            table_exists = await conn.scalar(
                text('SELECT to_regclass(:tbl) IS NOT NULL'),
                {'tbl': full_name},
            )
            if not table_exists:
                if logger:
                    logger.warning(f"[SQLAlchemyEvents] {cls.__name__} has "
                                   f"{'event' if len(events) == 1 else 'events'} {', '.join(events)}, "
                                   f"but relation '{full_name}' does not exist")
                continue

            if table_exists:
                tg_sql = text("""
                              SELECT tgname
                              FROM pg_trigger
                              WHERE tgrelid = to_regclass(:tbl)
                                AND NOT tgisinternal
                              """)

                for row in await conn.execute(tg_sql, {'tbl': full_name}):
                    existing_triggers.add(row[0])

            added_events = []
            for event in sorted(events):
                l_event = event.lower()
                trig_name = f'sa_{schema}_{table_name}_{l_event}_notify'

                if trig_name in existing_triggers:
                    continue

                added_events.append(event)
                await conn.execute(
                    text(f'DROP TRIGGER IF EXISTS {trig_name} ON {full_name};')
                )

                await conn.execute(
                    text(f"""
                            CREATE TRIGGER {trig_name}
                            AFTER {event} ON {full_name}
                            REFERENCING {'OLD' if event == SaEvent.DELETE else 'NEW'} 
                            TABLE AS {'old_rows' if event == SaEvent.DELETE else 'new_rows'}
                            FOR EACH STATEMENT
                            EXECUTE FUNCTION sqlalchemy_events_notify_{l_event}();
                         """)
                )

            if added_events and logger:
                logger.info(
                    f"Detected added {'event' if len(added_events) == 1 else 'events'} "
                    f"{', '.join(added_events)} for table '{full_name}'"
                )

        await conn.commit()