"""Repository for user-owned agent configurations (B3).

Agent content (SOUL.md, config.yaml) stays on disk. This repository tracks
owner metadata in agent_configs so ownership can be enforced on write endpoints.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.models.agent_config_row import AgentConfigRow


class AgentRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    async def list_by_owner(self, owner_id: str | None) -> list[AgentConfigRow]:
        async with self._sf() as session:
            stmt = select(AgentConfigRow).where(
                (AgentConfigRow.owner_id == owner_id)
                | (AgentConfigRow.is_shared == True)  # noqa: E712
                | (AgentConfigRow.is_system == True)  # noqa: E712
                | (AgentConfigRow.owner_id.is_(None))
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_by_owner_and_name(self, owner_id: str, name: str) -> AgentConfigRow | None:
        async with self._sf() as session:
            stmt = select(AgentConfigRow).where(
                (AgentConfigRow.owner_id == owner_id) & (AgentConfigRow.name == name)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def exists_for_owner(self, owner_id: str, name: str) -> bool:
        row = await self.get_by_owner_and_name(owner_id, name)
        return row is not None

    async def upsert(self, owner_id: str | None, name: str, is_shared: bool = False, is_system: bool = False) -> AgentConfigRow:
        async with self._sf() as session:
            async with session.begin():
                stmt = select(AgentConfigRow).where(
                    (AgentConfigRow.owner_id == owner_id) & (AgentConfigRow.name == name)
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()
                if existing:
                    existing.is_shared = is_shared
                    existing.is_system = is_system
                    await session.flush()
                    await session.refresh(existing)
                    return existing
                row = AgentConfigRow(owner_id=owner_id, name=name, is_shared=is_shared, is_system=is_system)
                session.add(row)
                await session.flush()
                await session.refresh(row)
                return row

    async def delete(self, owner_id: str, name: str) -> bool:
        async with self._sf() as session:
            async with session.begin():
                stmt = delete(AgentConfigRow).where(
                    (AgentConfigRow.owner_id == owner_id) & (AgentConfigRow.name == name)
                )
                result = await session.execute(stmt)
                return result.rowcount > 0


_agent_repo: AgentRepository | None = None


def get_agent_repo() -> AgentRepository:
    global _agent_repo
    if _agent_repo is None:
        sf = get_session_factory()
        if sf is None:
            raise RuntimeError("Persistence backend is memory-mode; agent repository unavailable")
        _agent_repo = AgentRepository(sf)
    return _agent_repo
