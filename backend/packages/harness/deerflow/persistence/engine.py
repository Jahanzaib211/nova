"""Async SQLAlchemy engine lifecycle management.

Initializes at Gateway startup, provides session factory for
repositories, disposes at shutdown.

When database.backend="memory", init_engine is a no-op and
get_session_factory() returns None. Repositories must check for
None and fall back to in-memory implementations.

Also exports ``commit_with_lock_retry`` — a small helper that retries
``session.commit()`` on ``OperationalError: database is locked``. SQLite
with WAL serialises writers; two concurrent INSERTs into any table
(admin_audit, thread_meta, …) race for the write lock, and the loser
raises until ``busy_timeout`` elapses. The engine now sets
``busy_timeout=30000`` (see ``_enable_sqlite_wal`` below) which handles
the common case via lock-wait, but a 30 s wait is still too short when
two writes collide back-to-back across separate request handlers. This
helper adds the small bounded retry on top — used by both
``app.gateway.admin_ops.record_audit`` and
``deerflow.persistence.thread_meta.sql.ThreadMetaRepository`` so all
SQL writes share the same resilience contract.
"""

from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def _json_serializer(obj: object) -> str:
    """JSON serializer with ensure_ascii=False for Chinese character support."""
    return json.dumps(obj, ensure_ascii=False)


logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


async def _auto_create_postgres_db(url: str) -> None:
    """Connect to the ``postgres`` maintenance DB and CREATE DATABASE.

    The target database name is extracted from *url*.  The connection is
    made to the default ``postgres`` database on the same server using
    ``AUTOCOMMIT`` isolation (CREATE DATABASE cannot run inside a
    transaction).
    """
    from sqlalchemy import text
    from sqlalchemy.engine.url import make_url

    parsed = make_url(url)
    db_name = parsed.database
    if not db_name:
        raise ValueError("Cannot auto-create database: no database name in URL")

    # Connect to the default 'postgres' database to issue CREATE DATABASE
    maint_url = parsed.set(database="postgres")
    maint_engine = create_async_engine(maint_url, isolation_level="AUTOCOMMIT")
    try:
        async with maint_engine.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        logger.info("Auto-created PostgreSQL database: %s", db_name)
    finally:
        await maint_engine.dispose()


async def init_engine(
    backend: str,
    *,
    url: str = "",
    echo: bool = False,
    pool_size: int = 5,
    sqlite_dir: str = "",
) -> None:
    """Create the async engine and session factory, then auto-create tables.

    Args:
        backend: "memory", "sqlite", or "postgres".
        url: SQLAlchemy async URL (for sqlite/postgres).
        echo: Echo SQL to log.
        pool_size: Postgres connection pool size.
        sqlite_dir: Directory to create for SQLite (ensured to exist).
    """
    global _engine, _session_factory

    if backend == "memory":
        logger.info("Persistence backend=memory -- ORM engine not initialized")
        return

    if backend == "postgres":
        try:
            import asyncpg  # noqa: F401
        except ImportError:
            raise ImportError(
                "database.backend is set to 'postgres' but asyncpg is not installed.\n"
                "Install it with:\n"
                "    cd backend && uv sync --all-packages --extra postgres\n"
                "On the next `make dev` the postgres extra is auto-detected from\n"
                "config.yaml (database.backend: postgres) and reinstalled, so it\n"
                "will not be wiped again. Set UV_EXTRAS=postgres in .env to opt in\n"
                "explicitly. Or switch to backend: sqlite in config.yaml for\n"
                "single-node deployment."
            ) from None

    if backend == "sqlite":
        import os

        from sqlalchemy import event, text

        os.makedirs(sqlite_dir or ".", exist_ok=True)
        # aiosqlite's ``timeout`` kwarg is the busy timeout in *seconds* — it
        # tells the driver how long to wait for the SQLite write lock to clear
        # before raising ``OperationalError: database is locked``. The python
        # sqlite3 default is 5 s, which is too short when two ops-console
        # requests both INSERT into ``admin_audit`` at the same moment: with
        # WAL one of them waits for the other, but the wait is bounded by this
        # timeout. 30 s lines up with the retry budget in
        # ``commit_with_lock_retry`` and is the standard recommendation for any
        # busy SQLite workload (SQLite docs §5.0).
        _engine = create_async_engine(
            url,
            echo=echo,
            json_serializer=_json_serializer,
            connect_args={"timeout": 30},
        )

        # Enable WAL on every new connection. SQLite PRAGMA settings are
        # per-connection, so we wire the listener AND apply them at startup
        # (belt-and-braces: aiosqlite's connect listener can race with the
        # initial schema bootstrap, and the production gateway proved this
        # — a stale pool inherited busy_timeout=5000 even after the fix
        # landed, so we explicitly run the PRAGMAs once after create_all).
        # The companion ``synchronous=NORMAL`` is the safe-and-fast pairing
        # — fsync only at WAL checkpoint boundaries instead of every commit.
        # ``busy_timeout`` is set as a belt-and-braces to the aiosqlite
        # ``timeout`` kwarg above: aiosqlite delegates to the underlying
        # sqlite3 driver, but PRAGMA wins over any connect-arg default and
        # also applies to reads (BEGIN IMMEDIATE etc.). 30 s matches.
        @event.listens_for(_engine.sync_engine, "connect")
        def _enable_sqlite_wal(dbapi_conn, _record):  # noqa: ARG001 — SQLAlchemy contract
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA synchronous=NORMAL;")
                cursor.execute("PRAGMA busy_timeout=30000;")
                cursor.execute("PRAGMA foreign_keys=ON;")
            finally:
                cursor.close()
    elif backend == "postgres":
        _engine = create_async_engine(
            url,
            echo=echo,
            pool_size=pool_size,
            pool_pre_ping=True,
            json_serializer=_json_serializer,
        )
    else:
        raise ValueError(f"Unknown persistence backend: {backend!r}")

    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    # Auto-create tables (dev convenience). Production should use Alembic.
    from deerflow.persistence.base import Base

    # Import all models so Base.metadata discovers them.
    # When no models exist yet (scaffolding phase), this is a no-op.
    try:
        import deerflow.persistence.models  # noqa: F401
    except ImportError:
        # Models package not yet available — tables won't be auto-created.
        # This is expected during initial scaffolding or minimal installs.
        logger.debug("deerflow.persistence.models not found; skipping auto-create tables")

    try:
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:
        if backend == "postgres" and "does not exist" in str(exc):
            # Database not yet created — attempt to auto-create it, then retry.
            await _auto_create_postgres_db(url)
            # Rebuild engine against the now-existing database
            await _engine.dispose()
            _engine = create_async_engine(url, echo=echo, pool_size=pool_size, pool_pre_ping=True, json_serializer=_json_serializer)
            _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
            async with _engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        else:
            raise

    # Belt-and-braces PRAGMA bootstrap for SQLite. The ``connect`` listener
    # fires for every new DBAPI connection SQLAlchemy opens, but the live
    # gateway had connections whose pool slots were checked out before
    # init_engine ran the listener registration, and busy_timeout=5000
    # was inherited instead of 30000. Run the PRAGMAs explicitly on a
    # fresh connection so any pool slot that subsequently picks up this
    # connection carries the right values. The listener handles new
    # connections from then on. This is a no-op for non-SQLite backends.
    if backend == "sqlite":
        try:
            async with _engine.begin() as conn:
                await conn.execute(text("PRAGMA journal_mode=WAL;"))
                await conn.execute(text("PRAGMA synchronous=NORMAL;"))
                await conn.execute(text("PRAGMA busy_timeout=30000;"))
                await conn.execute(text("PRAGMA foreign_keys=ON;"))
            logger.info("SQLite PRAGMAs applied at startup: WAL, synchronous=NORMAL, busy_timeout=30000")
        except Exception:  # noqa: BLE001 — best-effort; the listener is still authoritative
            logger.exception("Failed to apply SQLite PRAGMAs at startup")

    logger.info("Persistence engine initialized: backend=%s", backend)


async def init_engine_from_config(config) -> None:
    """Convenience: init engine from a DatabaseConfig object."""
    if config.backend == "memory":
        await init_engine("memory")
        return
    await init_engine(
        backend=config.backend,
        url=config.app_sqlalchemy_url,
        echo=config.echo_sql,
        pool_size=config.pool_size,
        # `_resolved_sqlite_dir`, not the raw `sqlite_dir` field — the raw
        # field is a relative default (".deer-flow/data") that `sqlite_path`
        # / `app_sqlalchemy_url` never actually point at; only the resolved
        # (absolute, DEER_FLOW_SQLITE_DIR-aware) property matches the real
        # DB path. Passing the raw field here silently created an unused
        # directory under CWD instead of failing loudly — readOnlyRootFilesystem
        # in k8s turned that into a startup crash instead of a silent no-op.
        sqlite_dir=config._resolved_sqlite_dir if config.backend == "sqlite" else "",
    )


def get_session_factory() -> async_sessionmaker[AsyncSession] | None:
    """Return the async session factory, or None if backend=memory."""
    return _session_factory


def get_engine() -> AsyncEngine | None:
    """Return the async engine, or None if not initialized."""
    return _engine


async def close_engine() -> None:
    """Dispose the engine, release all connections."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        logger.info("Persistence engine closed")
    _engine = None
    _session_factory = None


# ---------------------------------------------------------------------------
# SQLite write-lock retry
# ---------------------------------------------------------------------------

# 3 attempts × exponential backoff gives a ~750 ms total budget — small enough
# that a real outage surfaces fast (the last attempt re-raises), large enough
# that two near-simultaneous writes into any hot table (admin_audit,
# thread_meta, …) settle on the first or second attempt.
_COMMIT_RETRY_ATTEMPTS = 3
_COMMIT_RETRY_BACKOFF_S = (0.05, 0.2, 0.5)


def _is_sqlite_locked_error(exc: BaseException) -> bool:
    """True for the ``OperationalError`` raised when SQLite cannot acquire the
    database write lock within ``busy_timeout``.

    Matches the aiosqlite dialect's wrapped message (``"database is locked"``)
    and the legacy ``"database table is locked"`` form. Anything else
    (network error, schema mismatch, missing column) is *not* a lock and must
    not be retried, otherwise a real bug is masked.
    """
    msg = str(exc)
    return "database is locked" in msg or "database table is locked" in msg


async def commit_with_lock_retry(
    session: AsyncSession,
    *,
    logger_: logging.Logger | None = None,
    attempts: int = _COMMIT_RETRY_ATTEMPTS,
    backoff: tuple[float, ...] = _COMMIT_RETRY_BACKOFF_S,
) -> None:
    """Commit the current transaction, retrying on ``OperationalError:
    database is locked``. Up to ``len(backoff) + 1`` attempts.

    On retry, the failed transaction is rolled back so the next attempt can
    begin a fresh one (SQLAlchemy auto-begins on the next ``execute()``).
    Non-locked ``OperationalError``s propagate immediately so real bugs
    surface; so does exhaustion of the retry budget.

    This helper exists in the persistence engine so it can be shared across
    every SQL write site (``admin_audit`` writes, ``thread_meta`` writes,
    any future repository) — the same resilience contract regardless of
    caller. The audit-specific import path lives in
    ``app.gateway.admin_ops`` for historical reasons; new call sites should
    use this helper directly.
    """
    log = logger_ or logger
    last_exc: BaseException | None = None
    for attempt in range(max(1, attempts)):
        try:
            await session.commit()
            return
        except OperationalError as exc:
            if not _is_sqlite_locked_error(exc):
                raise
            last_exc = exc
            try:
                await session.rollback()
            except Exception:  # noqa: BLE001 — rollback failure is best-effort
                pass
            if attempt < attempts - 1 and attempt < len(backoff):
                wait_s = backoff[attempt]
                log.warning(
                    "sqlite commit hit 'database is locked' (attempt %d/%d), sleeping %dms",
                    attempt + 1,
                    attempts,
                    int(wait_s * 1000),
                )
                await asyncio.sleep(wait_s)
    assert last_exc is not None
    raise last_exc
