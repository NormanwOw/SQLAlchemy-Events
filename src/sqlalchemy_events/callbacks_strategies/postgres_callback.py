import asyncio
import json
import inspect
import signal
import sys

from .base import SaEventsCallbacksStrategy
from ..registry import get_event_handlers
from ..types import Handler
from ..utils import _process_pool


signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))


class PostgresCallback(SaEventsCallbacksStrategy):

    def __init__(self) -> None:
        self.__handlers = get_event_handlers()

    async def handle(self, *args, **kwargs):
        data = json.loads(args[3] or '{}')
        trigger = data['op']
        rows = data['rows']
        trig_name = f'sa_{data['table']}_{trigger}_notify'
        handlers: list[Handler] = self.__handlers.get(trig_name, [])
        if not handlers:
            return

        loop = asyncio.get_running_loop()
        tasks = []

        for handler in handlers:
            func = handler.func
            sig = inspect.signature(func)

            params = list(sig.parameters.values())
            rows_index = None

            for i, p in enumerate(params):
                if p.name == 'rows':
                    rows_index = i
                    break

            def build_args():
                args = []
                kwargs = {}

                if rows_index is not None:
                    if not any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params):
                        args.insert(rows_index, rows)
                    else:
                        kwargs['rows'] = rows
                return args, kwargs

            args, kwargs = build_args()

            if asyncio.iscoroutinefunction(func):
                tasks.append(func(*args, **kwargs))
            else:
                tasks.append(
                    loop.run_in_executor(
                        _process_pool,
                        func,
                        *args,
                    )
                )

        return await asyncio.gather(*tasks)