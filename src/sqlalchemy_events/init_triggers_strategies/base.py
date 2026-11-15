from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy.orm import DeclarativeBase

from ..config import EventsConfig
from ..types import SaEvent

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True)
class TriggerTarget:
    model: type[DeclarativeBase]
    event: SaEvent
    needs_rows: bool


@dataclass(frozen=True)
class TriggerPlan:
    model: type[DeclarativeBase]
    schema: str
    table: str
    event: SaEvent
    needs_rows: bool
    channel: str
    trigger_name: str
    function_name: str
    id_columns: tuple[str, ...]

    @property
    def transition_table(self) -> str:
        return 'old_rows' if self.event is SaEvent.DELETE else 'new_rows'

    @property
    def is_delete(self) -> bool:
        return self.event is SaEvent.DELETE


@dataclass
class SyncResult:
    created_triggers: list[str] = field(default_factory=list)
    replaced_triggers: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    missing_tables: list[str] = field(default_factory=list)
    statements: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.created_triggers or self.replaced_triggers or self.functions)

    def summary(self) -> str:
        parts = [
            f'{len(self.created_triggers)} trigger(s) created',
            f'{len(self.replaced_triggers)} trigger(s) replaced',
            f'{len(self.functions)} function(s) updated',
        ]
        if self.missing_tables:
            parts.append(f'{len(self.missing_tables)} table(s) missing')
        if self.dry_run:
            parts.append('dry run')
        return ', '.join(parts)


@dataclass
class DropResult:
    triggers: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    statements: list[str] = field(default_factory=list)
    dry_run: bool = False

    def summary(self) -> str:
        parts = [
            f'{len(self.triggers)} trigger(s) dropped',
            f'{len(self.functions)} function(s) dropped',
        ]
        if self.dry_run:
            parts.append('dry run')
        return ', '.join(parts)


@dataclass(frozen=True)
class ManagedTrigger:
    schema: str
    table: str
    trigger: str
    function: str
    event: str | None = None
    channel: str | None = None


class InitTriggersStrategy(ABC):
    """Builds and applies the database side of the event system."""

    @abstractmethod
    def build_plans(
        self,
        targets: Sequence[TriggerTarget],
        *,
        default_schema: str,
        config: EventsConfig,
    ) -> list[TriggerPlan]:
        raise NotImplementedError

    @abstractmethod
    async def sync(
        self,
        conn: AsyncConnection,
        plans: Sequence[TriggerPlan],
        *,
        config: EventsConfig,
        dry_run: bool = False,
    ) -> SyncResult:
        raise NotImplementedError

    @abstractmethod
    async def drop(
        self,
        conn: AsyncConnection,
        *,
        config: EventsConfig,
        default_schema: str,
        plans: Sequence[TriggerPlan] | None = None,
        all_managed: bool = False,
        dry_run: bool = False,
    ) -> DropResult:
        raise NotImplementedError

    @abstractmethod
    async def list_managed(
        self,
        conn: AsyncConnection,
        *,
        config: EventsConfig,
        default_schema: str,
    ) -> list[ManagedTrigger]:
        raise NotImplementedError

    @abstractmethod
    def render_enable(
        self,
        plans: Sequence[TriggerPlan],
        *,
        config: EventsConfig,
    ) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def render_disable(
        self,
        plans: Sequence[TriggerPlan],
        *,
        config: EventsConfig,
    ) -> list[str]:
        raise NotImplementedError
