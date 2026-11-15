class SQLAlchemyEventsError(Exception):
    """Base class for all errors raised by the library."""


class HandlersDiscoveryError(SQLAlchemyEventsError):
    """Handler modules cannot be imported or discovered."""


class ConfigurationError(SQLAlchemyEventsError, RuntimeError):
    """The library is configured incorrectly."""


class InvalidModelEventError(ConfigurationError):
    """A model is not configured for the requested event tracking."""


class EventHandlerConfigurationError(ConfigurationError):
    """An event handler is registered incorrectly."""


class UnsupportedDialectError(SQLAlchemyEventsError, ValueError):
    """The database dialect is not supported."""


class ListenerError(SQLAlchemyEventsError):
    """The LISTEN/NOTIFY listener cannot operate."""


class NotificationPayloadError(SQLAlchemyEventsError, ValueError):
    """A notification payload is malformed or unsupported."""


class HandlerExecutionError(SQLAlchemyEventsError):
    """A handler failed while dispatching a synthetic event."""
