import sys

_GLOBAL_KEY = 'sqlalchemy_events._global_registry'

if _GLOBAL_KEY not in sys.modules:
    class _Registry:
        handlers = {}

    sys.modules[_GLOBAL_KEY] = _Registry()

_registry = sys.modules[_GLOBAL_KEY]


def get_event_handlers() -> dict:
    return _registry.handlers