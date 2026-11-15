import uuid

from sqlalchemy import UUID, Column, Integer, String
from sqlalchemy.orm import DeclarativeBase

from sqlalchemy_events import SaEvent, with_events


class Base(DeclarativeBase):
    pass


@with_events([SaEvent.INSERT, SaEvent.UPDATE, SaEvent.DELETE])
class UserModel(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    name = Column(String)


@with_events([SaEvent.INSERT, SaEvent.UPDATE, SaEvent.DELETE])
class MixedCaseModel(Base):
    __tablename__ = 'MixedCase'

    id = Column(Integer, primary_key=True)


@with_events([SaEvent.INSERT, SaEvent.UPDATE, SaEvent.DELETE])
class CompositeModel(Base):
    __tablename__ = 'composite'

    a = Column(Integer, primary_key=True)
    b = Column(Integer, primary_key=True)


@with_events([SaEvent.INSERT, SaEvent.UPDATE, SaEvent.DELETE])
class CustomIdModel(Base):
    __tablename__ = 'custom_ids'
    __events_id__ = 'external_id'

    pk = Column(Integer, primary_key=True)
    external_id = Column(Integer, nullable=False)


@with_events([SaEvent.INSERT, SaEvent.UPDATE, SaEvent.DELETE])
class UuidModel(Base):
    __tablename__ = 'uuid_users'

    id = Column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )


@with_events([SaEvent.INSERT, SaEvent.UPDATE, SaEvent.DELETE])
class FailingModel(Base):
    __tablename__ = 'failing'

    id = Column(Integer, primary_key=True)


@with_events([SaEvent.INSERT, SaEvent.UPDATE, SaEvent.DELETE])
class SyncModel(Base):
    __tablename__ = 'sync_models'

    id = Column(Integer, primary_key=True)


@with_events([SaEvent.INSERT, SaEvent.UPDATE, SaEvent.DELETE])
class MethodModel(Base):
    __tablename__ = 'method_models'

    id = Column(Integer, primary_key=True)


class PlainModel(Base):
    __tablename__ = 'plain'

    id = Column(Integer, primary_key=True)
