from abc import ABC, abstractmethod
from typing import Type

from sqlalchemy.orm import DeclarativeBase


class InitTriggersStrategy(ABC):

    @abstractmethod
    async def __call__(self, model_list: list[Type[DeclarativeBase]], conn, logger):
        raise NotImplementedError


