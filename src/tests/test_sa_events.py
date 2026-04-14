import logging

import pytest

from sqlalchemy_events import SQLAlchemyEvents


async def test_invalid_engine_type():
    with pytest.raises(RuntimeError):
        await SQLAlchemyEvents(engine='not_engine', autodiscover_paths=[])()


async def test_empty_autodiscover_paths_logs_warning(caplog, engine):
    caplog.set_level('WARNING', logger='SQLAlchemyEvents')
    await SQLAlchemyEvents(engine=engine, autodiscover_paths=[], verbose=True)()
    assert 'No autodiscover paths' in caplog.text


async def test_no_logs_when_verbose_disabled(caplog, engine):
    await SQLAlchemyEvents(engine=engine, autodiscover_paths=[], verbose=False)()
    assert caplog.text == ''


async def test_custom_logger_used(caplog, engine):
    logger = logging.getLogger('test')
    await SQLAlchemyEvents(engine=engine, autodiscover_paths=[], logger=logger)()
    assert 'SQLAlchemyEvents' in caplog.text