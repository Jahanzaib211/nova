"""Nova staging backup job — runs inside the hourly CronJob (cronjob-backup.yaml).

Backs up the two pieces of state that actually matter for recovery:
  - deerflow.db (SQLite, WAL mode) via sqlite3's own online .backup() API,
    which is safe to run against a live database with concurrent writers
    (it does NOT need to stop the gateway/channels pods) — a raw file copy
    of a WAL-mode database can catch a mid-write, inconsistent state; the
    backup API cannot.
  - extensions_config.json — a small, infrequently-written file; a plain
    copy is fine.

Reuses the gateway image (already imported into k3s, already has Python's
sqlite3 stdlib module) rather than adding a new image/dependency just for
this.

Honest limitation (see k8s/ARCHITECTURE.md's DR section): this writes to a
hostPath on the SAME physical node as the cluster it's backing up, not a
second node or object storage — neither exists in this single-node setup.
A real node failure loses both the live data and every backup together.
This still protects against the much more common failure modes (bad
deploy, accidental data corruption/deletion, operator error), just not a
whole-node loss.
"""

from __future__ import annotations

import datetime
import os
import shutil
import sqlite3
import sys
import tarfile

SOURCE_DB = "/data/deer-flow/.deer-flow/data/deerflow.db"
SOURCE_EXTENSIONS_CONFIG = "/extensions-config/extensions_config.json"
BACKUP_ROOT = "/backup"
RETENTION_COUNT = int(os.environ.get("BACKUP_RETENTION_COUNT", "72"))  # 72 hourly = 3 days


def backup_sqlite(dest_dir: str) -> None:
    if not os.path.exists(SOURCE_DB):
        print(f"WARNING: {SOURCE_DB} does not exist yet (fresh install?) — skipping DB backup", file=sys.stderr)
        return
    src = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(os.path.join(dest_dir, "deerflow.db"))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    print(f"backed up {SOURCE_DB}")


def backup_extensions_config(dest_dir: str) -> None:
    if not os.path.exists(SOURCE_EXTENSIONS_CONFIG):
        print(f"WARNING: {SOURCE_EXTENSIONS_CONFIG} does not exist — skipping", file=sys.stderr)
        return
    shutil.copy2(SOURCE_EXTENSIONS_CONFIG, os.path.join(dest_dir, "extensions_config.json"))
    print(f"backed up {SOURCE_EXTENSIONS_CONFIG}")


def prune_old_backups() -> None:
    entries = sorted(f for f in os.listdir(BACKUP_ROOT) if f.startswith("nova-backup-") and f.endswith(".tar.gz"))
    excess = len(entries) - RETENTION_COUNT
    for name in entries[: max(excess, 0)]:
        path = os.path.join(BACKUP_ROOT, name)
        os.remove(path)
        print(f"pruned old backup {name}")


def main() -> None:
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    staging_dir = os.path.join(BACKUP_ROOT, f".staging-{ts}")
    os.makedirs(staging_dir, exist_ok=True)

    backup_sqlite(staging_dir)
    backup_extensions_config(staging_dir)

    archive_path = os.path.join(BACKUP_ROOT, f"nova-backup-{ts}.tar.gz")
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(staging_dir, arcname=ts)
    shutil.rmtree(staging_dir)
    print(f"wrote {archive_path} ({os.path.getsize(archive_path)} bytes)")

    prune_old_backups()


if __name__ == "__main__":
    main()
