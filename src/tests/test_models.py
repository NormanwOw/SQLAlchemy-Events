from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import DeclarativeBase

from sqlalchemy_events import SaEvent, with_events


class Base(DeclarativeBase):
    pass


@with_events([SaEvent.INSERT, SaEvent.UPDATE, SaEvent.DELETE])
class UserModel(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    name = Column(String)