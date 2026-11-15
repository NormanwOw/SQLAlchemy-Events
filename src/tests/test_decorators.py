from __future__ import annotations

import pytest
from sqlalchemy import Column, Integer

from sqlalchemy_events import SaEvent, with_events
from sqlalchemy_events.exceptions import InvalidModelEventError
from tests.test_models import Base, UserModel


def test_with_events_on_incorrect_class_type() -> None:
    with pytest.raises(InvalidModelEventError):

        @with_events([SaEvent.INSERT])
        class NotAModel:
            pass


def test_incorrect_event_type() -> None:
    with pytest.raises(InvalidModelEventError):

        @with_events(['incorrect_event_type'])
        class Model(Base):
            __tablename__ = 'test_model'

            id = Column(Integer, primary_key=True)


def test_empty_events() -> None:
    with pytest.raises(TypeError):
        with_events()

    with pytest.raises(InvalidModelEventError):
        with_events([])


def test_string_is_rejected() -> None:
    with pytest.raises(InvalidModelEventError):
        with_events('insert')


def test_events_in_model() -> None:
    assert UserModel.events.INSERT
    assert UserModel.events.UPDATE
    assert UserModel.events.DELETE
    assert UserModel.__events__ == {
        SaEvent.INSERT,
        SaEvent.UPDATE,
        SaEvent.DELETE,
    }


def test_events_are_unique() -> None:
    @with_events([SaEvent.INSERT, SaEvent.INSERT])
    class Model(Base):
        __tablename__ = 'dedup_model'

        id = Column(Integer, primary_key=True)

    assert Model.__events__ == {SaEvent.INSERT}
