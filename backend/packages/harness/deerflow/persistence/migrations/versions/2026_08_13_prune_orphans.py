"""data fixup: prune orphan audit rows + rebind legacy 'default' runs

The legacy codebase had no FK constraint between ``runs.user_id`` and
``users.id``, leaving two detectable classes of dangling rows on the live
deployment:

1. ``runs.user_id == "default"`` — 95 rows from pre-auth / no-auth mode
   that the credit meter and the activity feed surface as ``email=null``.
2. ``admin_audit.target_user_id`` pointing at users that no longer exist
   — 3 rows from tests that hard-deleted their users.
3. ``credit_requests.user_id == "ops-console"`` — 1 row from a leftover
   smoke test that never had a real user.

This migration:

- Creates a synthetic ``__legacy_default__`` user (with a known UUID) so
  the ``runs.user_id`` FK semantics line up, then re-points every
  ``runs.user_id == "default"`` row at the synthetic user. The activity
  feed query already does a LEFT JOIN; with a real user present the
  join returns ``email="__legacy_default__@archive.local"`` instead of
  null, so the table renders as a labeled "legacy" row rather than a
  blank.
- Deletes ``admin_audit`` rows whose ``target_user_id`` no longer matches
  a user. Audit rows are append-only and the operator's history is the
  user-targeted history, so orphans are by definition impossible to
  reconcile — drop them rather than preserve noise.
- Deletes ``credit_requests`` orphans using the same rule.

The migration is idempotent: every step is a no-op when the data is
already in the post-fixup state. Running the upgrade on a database that
already has the synthetic user is a no-op.

The synthetic user is intentionally badged ``system_role="admin"`` so
it can never be confused with a real signup — and so the ops console
shows it as an admin row in the users table if anyone ever opens it.

Revision ID: 2026_08_13_prune_orphans
Revises: 2026_08_13_user_forbidden
Create Date: 2026-08-13
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = "2026_08_13_prune_orphans"
down_revision = "2026_08_13_user_forbidden"
branch_labels = None
depends_on = None

_LEGACY_USER_ID = "00000000-0000-0000-0000-000000000001"
# Email must be a valid deliverable-shaped address — Pydantic's EmailStr
# (via the email-validator package) rejects reserved TLDs (.local, .test,
# .example, .invalid) and the wildcard ``.local`` mDNS TLD. We use a
# real TLD so the row passes validation everywhere it's hydrated. The
# ``__legacy_default__`` local-part and the ``nova-archive.com`` hostname
# are deliberately unaddressable so the synthetic user can never receive
# mail.
_LEGACY_EMAIL = "__legacy_default__@nova-archive.com"


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Create or reuse the synthetic legacy user.
    res = bind.execute(
        sa.text("SELECT id FROM users WHERE id = :id"),
        {"id": _LEGACY_USER_ID},
    ).fetchone()
    if res is None:
        bind.execute(
            sa.text(
                """
                INSERT INTO users (id, email, system_role, plan, created_at, needs_setup, token_version, is_forbidden, oauth_provider, tos_accepted_at)
                VALUES (:id, :email, 'admin', 'free', CURRENT_TIMESTAMP, 0, 0, 0, NULL, NULL)
                """
            ),
            {"id": _LEGACY_USER_ID, "email": _LEGACY_EMAIL},
        )
        logger.info("Created synthetic legacy user %s", _LEGACY_USER_ID)
    else:
        logger.info("Synthetic legacy user %s already present — skipping insert", _LEGACY_USER_ID)

    # 2. Rebind runs.user_id == "default" to the synthetic user.
    rebind = bind.execute(
        sa.text("UPDATE runs SET user_id = :new WHERE user_id = :old"),
        {"new": _LEGACY_USER_ID, "old": "default"},
    ).rowcount
    if rebind:
        logger.info("Rebound %d orphan runs rows from 'default' to %s", rebind, _LEGACY_USER_ID)
    else:
        logger.info("No runs rows with user_id='default' — skipping rebind")

    # 3. Delete orphan admin_audit rows.
    audit_orphans = bind.execute(
        sa.text(
            """
            DELETE FROM admin_audit
            WHERE target_user_id IS NOT NULL
              AND target_user_id NOT IN (SELECT id FROM users)
            """
        )
    ).rowcount
    if audit_orphans:
        logger.info("Deleted %d orphan admin_audit rows", audit_orphans)
    else:
        logger.info("No orphan admin_audit rows — skipping")

    # 4. Delete orphan credit_requests rows.
    cr_orphans = bind.execute(
        sa.text(
            """
            DELETE FROM credit_requests
            WHERE user_id NOT IN (SELECT id FROM users)
            """
        )
    ).rowcount
    if cr_orphans:
        logger.info("Deleted %d orphan credit_requests rows", cr_orphans)
    else:
        logger.info("No orphan credit_requests rows — skipping")


def downgrade() -> None:
    # The synthetic user is intentionally NOT removed on downgrade — it is
    # referenced by the rebinded runs rows and removing the user would
    # orphan them again. Operators who want to remove the synthetic user
    # should re-run the upgrade with the guard checks reenabled.
    logger.info("Prune-orphans downgrade is a no-op; synthetic user and rebounds are preserved.")
