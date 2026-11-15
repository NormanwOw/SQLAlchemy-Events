from importlib.metadata import PackageNotFoundError, version

from .asgi import SQLAlchemyEventsMiddleware, sa_events_lifespan
from .config import EventsConfig
from .core import SQLAlchemyEvents
from .decorators import with_events
from .events import SaEvents
from .exceptions import (
    ConfigurationError,
    EventHandlerConfigurationError,
    HandlerExecutionError,
    HandlersDiscoveryError,
    InvalidModelEventError,
    ListenerError,
    NotificationPayloadError,
    SQLAlchemyEventsError,
    UnsupportedDialectError,
)
from .handlers import sa_delete_handler, sa_insert_handler, sa_update_handler
from .metrics import Health, Metrics
from .types import SaEvent, SyncExecutorKind

try:
    __version__ = version('sqlalchemy-events-lib')
except PackageNotFoundError:
    __version__ = '0.5.0'

__all__ = [
    'ConfigurationError',
    'EventHandlerConfigurationError',
    'EventsConfig',
    'HandlerExecutionError',
    'HandlersDiscoveryError',
    'Health',
    'InvalidModelEventError',
    'ListenerError',
    'Metrics',
    'NotificationPayloadError',
    'SQLAlchemyEvents',
    'SQLAlchemyEventsError',
    'SQLAlchemyEventsMiddleware',
    'SaEvent',
    'SaEvents',
    'SyncExecutorKind',
    'UnsupportedDialectError',
    'sa_delete_handler',
    'sa_events_lifespan',
    'sa_insert_handler',
    'sa_update_handler',
    'with_events',
    '__version__',
]
