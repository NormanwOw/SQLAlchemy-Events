from sqlalchemy import text


async def test_is_created_triggers(session):
    trigger_names = [
        'sa_users_delete_notify',
        'sa_users_insert_notify',
        'sa_users_update_notify',
    ]

    result = await session.execute(
        text("""
             SELECT COUNT(*)
             FROM pg_trigger t
                      JOIN pg_class c ON t.tgrelid = c.oid
             WHERE c.relname = 'users'
               AND t.tgname = ANY (:trigger_names)
               AND NOT t.tgisinternal
             """),
        {'trigger_names': trigger_names}
    )

    count = result.scalar()

    assert count == len(trigger_names)


async def test_is_created_function(session):
    result = await session.execute(
        text("""
             SELECT EXISTS (SELECT 1
                            FROM pg_proc
                            WHERE proname = 'sqlalchemy_events')
             """)
    )
    exists = result.scalar()

    assert exists is True