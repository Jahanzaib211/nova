"""CLI tool to reset an admin password.

Usage:
    python -m app.gateway.auth.reset_admin
    python -m app.gateway.auth.reset_admin --email admin@example.com

Writes the new password to ``.deer-flow/admin_initial_credentials.txt``
(mode 0600) instead of printing it, so CI / log aggregators never see
the cleartext secret.

The HTTP ``POST /api/v1/admin/users/{user_id}/reset-password`` endpoint
shares the same code path via :func:`reset_user_password` below.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys

from sqlalchemy import select

from app.gateway.auth.credential_file import write_initial_credentials
from app.gateway.auth.password import hash_password
from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
from deerflow.persistence.user.model import UserRow


async def reset_user_password(repo: SQLiteUserRepository, user_email: str | None) -> tuple[str, str] | None:
    """Reset a user's password server-side.

    Returns ``(email, new_password)`` on success, ``None`` if the user
    cannot be found (CLI prints; HTTP returns 404). Either way the
    underlying effects are: ``password_hash`` re-bcrypted, ``token_version``
    bumped (invalidates all sessions), ``needs_setup=True`` so the user
    is forced through the setup flow on next login.

    Caller is responsible for deciding whether to write the credentials
    to the 0600 file (CLI path) or return the plaintext over the wire
    (HTTP path). Both are acceptable for an operator-only god-mode
    surface but the trade-offs differ — the CLI is for headless
    operations, the HTTP endpoint is for the interactive console.
    """
    if user_email:
        user = await repo.get_user_by_email(user_email)
    else:
        # Find first admin via direct SELECT — repository does not
        # expose a "first admin" helper and we do not want to add
        # one just for this CLI.
        from deerflow.persistence.engine import get_session_factory

        sf = get_session_factory()
        if sf is None:
            return None
        async with sf() as session:
            stmt = select(UserRow).where(UserRow.system_role == "admin").limit(1)
            row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        user = await repo.get_user_by_id(row.id)

    if user is None:
        return None

    new_password = secrets.token_urlsafe(16)
    user.password_hash = hash_password(new_password)
    user.token_version += 1
    user.needs_setup = True
    await repo.update_user(user)
    return user.email, new_password


async def _run(email: str | None) -> int:
    from deerflow.config import get_app_config
    from deerflow.persistence.engine import (
        close_engine,
        get_session_factory,
        init_engine_from_config,
    )

    config = get_app_config()
    await init_engine_from_config(config.database)
    try:
        sf = get_session_factory()
        if sf is None:
            print("Error: persistence engine not available (check config.database).", file=sys.stderr)
            return 1

        repo = SQLiteUserRepository(sf)
        result = await reset_user_password(repo, email)
        if result is None:
            if email:
                print(f"Error: user '{email}' not found.", file=sys.stderr)
            else:
                print("Error: no admin user found.", file=sys.stderr)
            return 1

        user_email, new_password = result
        cred_path = write_initial_credentials(user_email, new_password, label="reset")
        print(f"Password reset for: {user_email}")
        print(f"Credentials written to: {cred_path} (mode 0600)")
        print("Next login will require setup (new email + password).")
        return 0
    finally:
        await close_engine()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset admin password")
    parser.add_argument("--email", help="Admin email (default: first admin found)")
    args = parser.parse_args()

    exit_code = asyncio.run(_run(args.email))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
