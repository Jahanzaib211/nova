"""Tests for ModelConfigRow ORM model."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from deerflow.persistence.base import Base
from deerflow.persistence.models.model_config_row import ModelConfigRow


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    SessionLocal = sessionmaker(bind=engine)
    sess = SessionLocal()
    yield sess
    sess.close()


class TestModelConfigRow:
    def test_create_minimal(self, session):
        row = ModelConfigRow(
            owner_id="user-1",
            name="my-model",
            model="gpt-4o",
        )
        session.add(row)
        session.commit()

        assert row.id is not None
        assert row.owner_id == "user-1"
        assert row.name == "my-model"
        assert row.model == "gpt-4o"
        assert row.model_use == "langchain_openai:ChatOpenAI"
        assert row.display_name is None
        assert row.description is None
        assert row.base_url is None
        assert row.has_api_key is False
        assert row.supports_thinking is False
        assert row.supports_reasoning_effort is False
        assert row.supports_vision is False
        assert row.amd_compute is None
        assert row.is_shared is False
        assert row.is_system is False
        assert row.created_at is not None
        assert row.updated_at is not None

    def test_create_full(self, session):
        row = ModelConfigRow(
            owner_id="user-2",
            name="local-llama",
            model="llama-3.1-70b",
            model_use="langchain_openai:ChatOpenAI",
            display_name="Local Llama 70B",
            description="Self-hosted llama on GPU",
            base_url="http://localhost:8081/v1",
            has_api_key=True,
            supports_thinking=True,
            supports_reasoning_effort=True,
            supports_vision=False,
            amd_compute="AMD Instinct MI300X (Fireworks)",
            is_shared=True,
            is_system=False,
        )
        session.add(row)
        session.commit()

        assert row.has_api_key is True
        assert row.supports_thinking is True
        assert row.is_shared is True

    def test_owner_id_nullable(self, session):
        row = ModelConfigRow(owner_id=None, name="global-model", model="gpt-4o")
        session.add(row)
        session.commit()
        assert row.owner_id is None

    def test_system_model(self, session):
        row = ModelConfigRow(owner_id=None, name="built-in", model="gpt-4o", is_system=True)
        session.add(row)
        session.commit()
        assert row.is_system is True
        assert row.is_shared is False

    def test_unique_owner_name_constraint(self, session):
        row1 = ModelConfigRow(owner_id="user-1", name="model-a", model="gpt-4o")
        session.add(row1)
        session.commit()

        row2 = ModelConfigRow(owner_id="user-1", name="model-a", model="gpt-4o-mini")
        session.add(row2)
        with pytest.raises(Exception):  # UNIQUE constraint
            session.commit()

    def test_updated_at_auto_on_commit(self, session):
        row = ModelConfigRow(owner_id="user-1", name="model-t", model="gpt-4o")
        session.add(row)
        session.commit()
        original_updated = row.updated_at

        row.display_name = "Updated Name"
        session.commit()
        assert row.updated_at >= original_updated

    def test_api_key_not_stored(self, session):
        row = ModelConfigRow(
            owner_id="user-1",
            name="secure-model",
            model="gpt-4o",
            has_api_key=True,
        )
        session.add(row)
        session.commit()

        result = session.get(ModelConfigRow, row.id)
        assert result.has_api_key is True
        # Raw key is never stored — only the has_api_key flag
        assert not hasattr(result, "api_key") or result.api_key is None or result.api_key == ""
