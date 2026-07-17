"""Tests for AgentConfigRow ORM model."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from deerflow.persistence.base import Base
from deerflow.persistence.models.agent_config_row import AgentConfigRow


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


class TestAgentConfigRow:
    def test_create_minimal(self, session):
        row = AgentConfigRow(owner_id="user-1", name="my-agent")
        session.add(row)
        session.commit()

        assert row.id is not None
        assert row.owner_id == "user-1"
        assert row.name == "my-agent"
        assert row.is_shared is False
        assert row.is_system is False
        assert row.created_at is not None
        assert row.updated_at is not None

    def test_create_shared(self, session):
        row = AgentConfigRow(owner_id="user-1", name="shared-agent", is_shared=True)
        session.add(row)
        session.commit()
        assert row.is_shared is True

    def test_owner_id_nullable(self, session):
        row = AgentConfigRow(owner_id=None, name="global-agent")
        session.add(row)
        session.commit()
        assert row.owner_id is None

    def test_system_agent(self, session):
        row = AgentConfigRow(owner_id=None, name="built-in-agent", is_system=True)
        session.add(row)
        session.commit()
        assert row.is_system is True
        assert row.owner_id is None

    def test_unique_owner_name_constraint(self, session):
        row1 = AgentConfigRow(owner_id="user-1", name="agent-x")
        session.add(row1)
        session.commit()

        row2 = AgentConfigRow(owner_id="user-1", name="agent-x")
        session.add(row2)
        with pytest.raises(Exception):  # UNIQUE constraint
            session.commit()

    def test_updated_at_auto_on_commit(self, session):
        row = AgentConfigRow(owner_id="user-1", name="agent-t")
        session.add(row)
        session.commit()
        original_updated = row.updated_at

        row.is_shared = True
        session.commit()
        assert row.updated_at >= original_updated

    def test_same_name_different_owners(self, session):
        row1 = AgentConfigRow(owner_id="user-1", name="agent-y")
        row2 = AgentConfigRow(owner_id="user-2", name="agent-y")
        session.add(row1)
        session.add(row2)
        session.commit()
        assert row1.id != row2.id
