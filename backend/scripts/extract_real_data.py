"""One-off: extract REAL users + runs from the production DB (read-only) into
a fresh, fully-migrated dev DB so the ops console shows real data without
touching the 6.6GB root-owned production file. Only users + runs are copied
(the tables the console reads); checkpointer/event blobs are skipped.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime


def _parse_dt(v):
    if v is None:
        return datetime.now(UTC)
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, tz=UTC)
    s = str(v)
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except ValueError:
        return datetime.now(UTC)


async def main() -> None:
    from deerflow.config import get_app_config
    from deerflow.persistence.base import Base
    from deerflow.persistence.engine import close_engine, get_engine, get_session_factory, init_engine_from_config
    from deerflow.persistence.run.model import RunRow
    from deerflow.persistence.user.model import UserRow

    # Read the real production DB immutably (never written to).
    real = sqlite3.connect("file:.deer-flow/data/deerflow.db?immutable=1", uri=True)
    users = real.execute("SELECT id, email, password_hash, system_role, created_at, oauth_provider, oauth_id, needs_setup, token_version FROM users").fetchall()
    runs = real.execute("SELECT run_id, thread_id, user_id, status, model_name, total_input_tokens, total_output_tokens, total_tokens, created_at FROM runs").fetchall()
    real.close()

    config = get_app_config()
    await init_engine_from_config(config.database)
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sf = get_session_factory()
    async with sf() as session:
        for u in users:
            session.add(
                UserRow(
                    id=u[0],
                    email=u[1],
                    password_hash=u[2],
                    system_role=u[3] or "user",
                    created_at=_parse_dt(u[4]),
                    oauth_provider=u[5],
                    oauth_id=u[6],
                    needs_setup=bool(u[7]),
                    token_version=u[8] or 0,
                    plan="free",  # real users default to free until operated on
                )
            )
        for r in runs:
            session.add(
                RunRow(
                    run_id=r[0],
                    thread_id=r[1] or "unknown",
                    user_id=r[2],
                    status=r[3] or "completed",
                    model_name=r[4],
                    total_input_tokens=r[5] or 0,
                    total_output_tokens=r[6] or 0,
                    total_tokens=r[7] or 0,
                    created_at=_parse_dt(r[8]),
                )
            )
        await session.commit()

    print(f"Extracted {len(users)} real users + {len(runs)} real runs into the dev DB.")
    await close_engine()


if __name__ == "__main__":
    asyncio.run(main())
