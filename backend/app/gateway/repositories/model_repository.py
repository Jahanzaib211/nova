"""Repository for user-owned model configurations (B3).

Provides async DB access for model_configs rows. Callers should use these
methods rather than raw SQL. The YAML runtime_models.yaml is kept as a read
source for backward compatibility; new writes go to DB only.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.config.model_config import ModelConfig
from deerflow.config.runtime_models import load_runtime_model_dicts, runtime_model_names, save_runtime_model_dicts
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.models.model_config_row import ModelConfigRow

logger = logging.getLogger(__name__)


class ModelRepository:
    """Async repository for runtime model configurations."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    async def list_by_owner(self, owner_id: str | None) -> list[ModelConfigRow]:
        """List model configs visible to an owner: own rows + shared + system + NULL-owner."""
        async with self._sf() as session:
            stmt = select(ModelConfigRow).where(
                (ModelConfigRow.owner_id == owner_id)
                | (ModelConfigRow.is_shared == True)  # noqa: E712
                | (ModelConfigRow.is_system == True)  # noqa: E712
                | (ModelConfigRow.owner_id.is_(None))
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_by_owner_and_name(self, owner_id: str, name: str) -> ModelConfigRow | None:
        """Get a specific model by owner + name, or None if not found / not accessible."""
        async with self._sf() as session:
            stmt = select(ModelConfigRow).where(
                (ModelConfigRow.owner_id == owner_id) & (ModelConfigRow.name == name)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def name_exists_for_owner(self, owner_id: str | None, name: str) -> bool:
        """Check if a name is already taken by a non-NULL-owner row (shared/system excluded)."""
        async with self._sf() as session:
            stmt = select(ModelConfigRow.id).where(
                (ModelConfigRow.owner_id == owner_id)
                & (ModelConfigRow.name == name)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def create(
        self,
        owner_id: str,
        name: str,
        model: str,
        model_use: str,
        display_name: str | None = None,
        description: str | None = None,
        base_url: str | None = None,
        has_api_key: bool = False,
        supports_thinking: bool = False,
        supports_reasoning_effort: bool = False,
        supports_vision: bool = False,
        amd_compute: str | None = None,
        is_shared: bool = False,
        is_system: bool = False,
    ) -> ModelConfigRow:
        """Create a new runtime model config row."""
        async with self._sf() as session:
            async with session.begin():
                row = ModelConfigRow(
                    owner_id=owner_id,
                    name=name,
                    model=model,
                    model_use=model_use,
                    display_name=display_name,
                    description=description,
                    base_url=base_url,
                    has_api_key=has_api_key,
                    supports_thinking=supports_thinking,
                    supports_reasoning_effort=supports_reasoning_effort,
                    supports_vision=supports_vision,
                    amd_compute=amd_compute,
                    is_shared=is_shared,
                    is_system=is_system,
                )
                session.add(row)
            await session.refresh(row)
            return row

    async def update(
        self,
        owner_id: str,
        name: str,
        model: str,
        model_use: str,
        display_name: str | None,
        description: str | None,
        base_url: str | None,
        has_api_key: bool,
        supports_thinking: bool,
        supports_reasoning_effort: bool,
        supports_vision: bool,
        amd_compute: str | None,
    ) -> ModelConfigRow | None:
        """Update an existing model config row. Returns the updated row or None."""
        async with self._sf() as session:
            async with session.begin():
                stmt = select(ModelConfigRow).where(
                    (ModelConfigRow.owner_id == owner_id) & (ModelConfigRow.name == name)
                )
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()
                if row is None:
                    return None
                row.model = model
                row.model_use = model_use
                row.display_name = display_name
                row.description = description
                row.base_url = base_url
                row.has_api_key = has_api_key
                row.supports_thinking = supports_thinking
                row.supports_reasoning_effort = supports_reasoning_effort
                row.supports_vision = supports_vision
                row.amd_compute = amd_compute
                return row

    async def delete(self, owner_id: str, name: str) -> bool:
        """Delete a model config row. Returns True if deleted, False if not found."""
        async with self._sf() as session:
            async with session.begin():
                stmt = delete(ModelConfigRow).where(
                    (ModelConfigRow.owner_id == owner_id) & (ModelConfigRow.name == name)
                )
                result = await session.execute(stmt)
                return result.rowcount > 0


_model_repo: ModelRepository | None = None


def get_model_repo() -> ModelRepository:
    global _model_repo
    if _model_repo is None:
        sf = get_session_factory()
        if sf is None:
            raise RuntimeError("Persistence backend is memory-mode; model repository unavailable")
        _model_repo = ModelRepository(sf)
    return _model_repo
