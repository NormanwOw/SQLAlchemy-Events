from abc import ABC, abstractmethod


class SaEventsCallbacksStrategy(ABC):

    @abstractmethod
    async def handle(self, *args, **kwargs):
        raise NotImplementedError