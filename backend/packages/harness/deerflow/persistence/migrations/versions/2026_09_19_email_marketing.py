"""Email-marketing tables: em_lists, em_contacts, em_list_members, em_templates,
em_campaigns, em_sends, em_events, em_suppressions, em_bounce_cursor.

Revision ID: 2026_09_19_email_marketing
Revises: 2026_09_19_jobs

Idempotent like every version here: create_all at boot creates these on a
fresh database, so the migration only acts on a database that predates the
tables. Column definitions mirror deerflow.persistence.email_marketing.model
exactly (pinned by tests/test_schema_snapshot.py + test_migrations_apply_clean.py).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "2026_09_19_email_marketing"
down_revision: str | None = "2026_09_19_jobs"
branch_labels = None
depends_on = None

_TABLES = (
    "em_lists",
    "em_contacts",
    "em_list_members",
    "em_templates",
    "em_campaigns",
    "em_sends",
    "em_events",
    "em_suppressions",
    "em_bounce_cursor",
)


def _ts(name: str, *, nullable: bool) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if "em_lists" not in existing:
        op.create_table(
            "em_lists",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("owner_user_id", sa.String(64), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            _ts("created_at", nullable=False),
            _ts("updated_at", nullable=False),
            sa.UniqueConstraint("owner_user_id", "name", name="uq_em_lists_owner_name"),
        )
        op.create_index("ix_em_lists_owner_user_id", "em_lists", ["owner_user_id"])

    if "em_contacts" not in existing:
        op.create_table(
            "em_contacts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("owner_user_id", sa.String(64), nullable=False),
            sa.Column("email", sa.String(320), nullable=False),
            sa.Column("email_normalized", sa.String(320), nullable=False),
            sa.Column("first_name", sa.String(128), nullable=True),
            sa.Column("last_name", sa.String(128), nullable=True),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("attributes_json", sa.JSON(), nullable=False),
            _ts("created_at", nullable=False),
            _ts("updated_at", nullable=False),
            sa.UniqueConstraint("owner_user_id", "email_normalized", name="uq_em_contacts_owner_email"),
        )
        op.create_index("ix_em_contacts_owner_user_id", "em_contacts", ["owner_user_id"])
        op.create_index("ix_em_contacts_status", "em_contacts", ["status"])

    if "em_list_members" not in existing:
        op.create_table(
            "em_list_members",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("list_id", sa.String(36), nullable=False),
            sa.Column("contact_id", sa.String(36), nullable=False),
            _ts("added_at", nullable=False),
            sa.UniqueConstraint("list_id", "contact_id", name="uq_em_list_members"),
        )
        op.create_index("ix_em_list_members_list_id", "em_list_members", ["list_id"])
        op.create_index("ix_em_list_members_contact_id", "em_list_members", ["contact_id"])

    if "em_templates" not in existing:
        op.create_table(
            "em_templates",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("owner_user_id", sa.String(64), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("subject", sa.String(998), nullable=False),
            sa.Column("html", sa.Text(), nullable=False),
            sa.Column("text", sa.Text(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False),
            _ts("created_at", nullable=False),
            _ts("updated_at", nullable=False),
        )
        op.create_index("ix_em_templates_owner_user_id", "em_templates", ["owner_user_id"])

    if "em_campaigns" not in existing:
        op.create_table(
            "em_campaigns",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("owner_user_id", sa.String(64), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("list_id", sa.String(36), nullable=False),
            sa.Column("template_id", sa.String(36), nullable=False),
            sa.Column("from_email", sa.String(320), nullable=False),
            sa.Column("from_name", sa.String(128), nullable=False),
            sa.Column("reply_to", sa.String(320), nullable=True),
            sa.Column("status", sa.String(16), nullable=False),
            _ts("scheduled_at", nullable=True),
            sa.Column("throttle_per_minute", sa.Integer(), nullable=True),
            sa.Column("stats_json", sa.JSON(), nullable=False),
            sa.Column("job_id", sa.String(36), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            _ts("created_at", nullable=False),
            _ts("started_at", nullable=True),
            _ts("finished_at", nullable=True),
        )
        op.create_index("ix_em_campaigns_owner_user_id", "em_campaigns", ["owner_user_id"])
        op.create_index("ix_em_campaigns_status", "em_campaigns", ["status"])

    if "em_sends" not in existing:
        op.create_table(
            "em_sends",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("owner_user_id", sa.String(64), nullable=False),
            sa.Column("campaign_id", sa.String(36), nullable=False),
            sa.Column("contact_id", sa.String(36), nullable=False),
            sa.Column("email", sa.String(320), nullable=False),
            sa.Column("message_id", sa.String(255), nullable=True),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("last_error", sa.Text(), nullable=True),
            _ts("sent_at", nullable=True),
            _ts("created_at", nullable=False),
            sa.UniqueConstraint("message_id"),
        )
        for name, cols in (
            ("ix_em_sends_owner_user_id", ["owner_user_id"]),
            ("ix_em_sends_campaign_id", ["campaign_id"]),
            ("ix_em_sends_contact_id", ["contact_id"]),
            ("ix_em_sends_status", ["status"]),
            ("ix_em_sends_campaign_status", ["campaign_id", "status"]),
        ):
            op.create_index(name, "em_sends", cols)

    if "em_events" not in existing:
        op.create_table(
            "em_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_user_id", sa.String(64), nullable=False),
            sa.Column("campaign_id", sa.String(36), nullable=True),
            sa.Column("send_id", sa.String(36), nullable=True),
            sa.Column("contact_id", sa.String(36), nullable=True),
            sa.Column("type", sa.String(16), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            _ts("created_at", nullable=False),
        )
        for name, cols in (
            ("ix_em_events_owner_user_id", ["owner_user_id"]),
            ("ix_em_events_campaign_id", ["campaign_id"]),
            ("ix_em_events_send_id", ["send_id"]),
            ("ix_em_events_contact_id", ["contact_id"]),
            ("ix_em_events_type", ["type"]),
            ("ix_em_events_created_at", ["created_at"]),
        ):
            op.create_index(name, "em_events", cols)

    if "em_suppressions" not in existing:
        op.create_table(
            "em_suppressions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_user_id", sa.String(64), nullable=False),
            sa.Column("email_normalized", sa.String(320), nullable=False),
            sa.Column("reason", sa.String(16), nullable=False),
            sa.Column("detail", sa.Text(), nullable=True),
            _ts("created_at", nullable=False),
            sa.UniqueConstraint("owner_user_id", "email_normalized", name="uq_em_suppressions_owner_email"),
        )
        op.create_index("ix_em_suppressions_owner_user_id", "em_suppressions", ["owner_user_id"])

    if "em_bounce_cursor" not in existing:
        op.create_table(
            "em_bounce_cursor",
            sa.Column("owner_user_id", sa.String(64), primary_key=True),
            sa.Column("mailbox", sa.String(320), nullable=False),
            sa.Column("last_uid", sa.Integer(), nullable=False),
            sa.Column("uidvalidity", sa.Integer(), nullable=True),
            _ts("last_polled_at", nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("has_processed", sa.Boolean(), nullable=False),
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_table(table)
