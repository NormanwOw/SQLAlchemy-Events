import pytest
from sqlalchemy import Column, Integer, String

from sqlalchemy_events import SaEvent, with_events
from tests.test_models import Base, UserModel


def test_with_events_on_incorrect_class_type():
    with pytest.raises(RuntimeError):
        @with_events([SaEvent.INSERT])
        class Test: ...


def test_incorrect_event_type():
    with pytest.raises(RuntimeError):
        @with_events(['incorrect_event_type'])
        class UserModel(Base):
            __tablename__ = 'test_model'

            id = Column(Integer, primary_key=True)
            name = Column(String)


def test_empty_events():
    with pytest.raises(TypeError):
        @with_events()
        class UserModel(Base):
            __tablename__ = 'test_model'

            id = Column(Integer, primary_key=True)
            name = Column(String)

    with pytest.raises(RuntimeError):
        @with_events([])
        class UserModel(Base):
            __tablename__ = 'test_model'

            id = Column(Integer, primary_key=True)
            name = Column(String)


def test_events_in_model():
    assert UserModel.events.INSERT
    assert UserModel.events.UPDATE
    assert UserModel.events.DELETE