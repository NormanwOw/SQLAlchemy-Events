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
    func_names = [
        'sqlalchemy_events_notify_insert',
        'sqlalchemy_events_notify_update',
        'sqlalchemy_events_notify_delete'
    ]
    result = await session.execute(
        text("""
             SELECT COUNT(*) FROM pg_proc WHERE proname = ANY (:func_names)
             """),
        {'func_names': func_names}
    )
    count = result.scalar()

    assert count == len(func_names)