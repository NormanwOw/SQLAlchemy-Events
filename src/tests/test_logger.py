from __future__ import annotations

import logging

from sqlalchemy_events.default_logger import DefaultLogger


def test_default_logger_methods(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger='SQLAlchemyEvents')
    logger = DefaultLogger()

    logger.debug('debug message')
    logger.info('info message')
    logger.warning('warning message')
    logger.error('error message', exc_info=False)

    assert 'debug message' in caplog.text
    assert 'info message' in caplog.text
    assert 'warning message' in caplog.text
    assert 'error message' in caplog.text
