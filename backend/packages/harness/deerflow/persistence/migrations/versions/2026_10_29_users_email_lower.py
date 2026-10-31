"""Users: canonical lowercase email + case-insensitive unique index.

Revision ID: 2026_10_29_users_email_lower
Revises: 2026_09_20_harness_tokens

Lookups became case-insensitive (``lower(email) = ?``) while the only unique
index stayed case-sensitive, so a second row differing only by casing could be
inserted and then made every lookup for that address ambiguous. This
lowercases stored emails and adds ``uq_users_email_lower``.

Idempotent like every version here. It refuses to run, rather than pick a
winner, if two existing rows already collide case-insensitively: merging
accounts is an operator decision, not a migration's.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "2026_10_29_users_email_lower"
down_revision: str | None = "2026_09_20_harness_tokens"
branch_labels = None
depends_on = None

_INDEX = "uq_users_email_lower"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "users" not in set(inspector.get_table_names()):
        return
    collisions = bind.execute(sa.text("SELECT lower(trim(email)) AS e, count(*) FROM users GROUP BY lower(trim(email)) HAVING count(*) > 1")).fetchall()
    if collisions:
        raise RuntimeError(f"users.email has case-insensitive duplicates, resolve them first: {[row[0] for row in collisions]}")
    op.execute("UPDATE users SET email = lower(trim(email)) WHERE email <> lower(trim(email))")
    # Raw DDL rather than an inspector check: SQLite's index reflection skips
    # expression indexes, so the index would look absent and be recreated.
    op.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX} ON users (lower(email))")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
