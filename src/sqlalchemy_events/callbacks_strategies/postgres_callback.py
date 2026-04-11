import asyncio
import json

from .base import SaEventsCallbacksStrategy
from ..registry import get_event_handlers


class PostgresCallback(SaEventsCallbacksStrategy):

    def __init__(self) -> None:
        self.__handlers = get_event_handlers()

    async def handle(self, *args, **kwargs):
        data = json.loads(args[3] or '{}')
        trig_name = data.get('trigger')

        if not trig_name:
            return

        handlers = self.__handlers.get(trig_name, [])
        if not handlers:
            return

        for handler in handlers:
            if asyncio.iscoroutinefunction(handler):
                await handler()
            else:
                handler()