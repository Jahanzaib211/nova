#!/usr/bin/env python3
"""Reclaim regenerable build artifacts from Nova's thread workspaces.

Why this exists
---------------
Every thread gets a workspace under ``.deer-flow/users/<user>/threads/<thread>/``
that the agent writes into, and nothing ever cleans it. By 2026-08-21 that was
7.3 GB across 115 workspaces on a disk that had already been taken down once by
unbounded growth (see ``prune-checkpoints.py`` for the 59 GB story).

What makes this different from the checkpoint pruner is *what* is big. A
workspace holds real deliverables — the code and documents the agent produced
for a user, which must never be deleted on a timer. But measuring the 2026-08-21
tree showed **99% of the bytes were regenerable**: ``node_modules``, ``.venv``,
``.next``, ``dist``, ``target``, ``__pycache__``. The actual work product was
~90 MB of the 7.3 GB.

So this does not prune workspaces. It prunes *build output inside* workspaces,
and keeps every deliverable regardless of age. A user who returns to an old
thread finds their files intact and reruns ``npm install``.

Retention
---------
A regenerable directory is deleted when its workspace has been idle for more
than ``--keep-days``. Idle means the newest mtime anywhere in the workspace, not
the directory's own mtime, which changes for reasons unrelated to the work.

Usage
-----
    scripts/prune-workspaces.py --dry-run
    scripts/prune-workspaces.py --keep-days 14
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

# Directory names whose contents can be rebuilt from a manifest that lives
# beside them (package.json, pyproject.toml, Cargo.toml, ...). Matched on the
# directory name at any depth.
#
# `.git` is deliberately NOT here: it is regenerable only if the work was
# pushed somewhere, and for an agent's scratch repo it usually was not — the
# history *is* the deliverable.
REGENERABLE = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        ".next",
        ".nuxt",
        ".turbo",
        ".parcel-cache",
        "dist",
        "build",
        "target",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".cache",
    }
)


def default_users_dir() -> Path:
    env = os.environ.get("DEER_FLOW_DATA_DIR")
    if env:
        return Path(env) / "users"
    return Path(__file__).resolve().parent.parent / "backend" / ".deer-flow" / "users"


def dir_size(path: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.stat(os.path.join(dirpath, name), follow_symlinks=False).st_size
            except OSError:
                pass
    return total


def newest_mtime(path: Path) -> float:
    """Newest mtime anywhere under `path`.

    The workspace directory's own mtime only tracks direct children, so a thread
    whose recent activity was three levels down looks stale. That would delete
    the dependencies of work still in progress.
    """
    newest = 0.0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                st = os.stat(os.path.join(dirpath, name), follow_symlinks=False)
            except OSError:
                continue
            if st.st_mtime > newest:
                newest = st.st_mtime
    return newest


def find_regenerable(workspace: Path) -> list[Path]:
    """Top-most regenerable directories under `workspace`.

    Pruned as we descend: once ``node_modules`` is selected there is no point
    walking into the nested ``node_modules`` inside it, and deleting the parent
    removes them anyway.
    """
    found: list[Path] = []
    for dirpath, dirnames, _filenames in os.walk(workspace):
        hits = [d for d in dirnames if d in REGENERABLE]
        for name in hits:
            found.append(Path(dirpath) / name)
        # Do not descend into what we already selected.
        dirnames[:] = [d for d in dirnames if d not in REGENERABLE]
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--users-dir", type=Path, default=default_users_dir())
    parser.add_argument(
        "--keep-days",
        type=float,
        default=float(os.environ.get("NOVA_WORKSPACE_KEEP_DAYS", "14")),
        help="Leave workspaces touched within this many days completely alone.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--manifest", type=Path, help="Write a JSONL record of what was removed.")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable summary.")
    args = parser.parse_args(argv)

    users_dir: Path = args.users_dir
    if not users_dir.is_dir():
        print(f"no users directory at {users_dir}", file=sys.stderr)
        return 0

    now = time.time()
    cutoff = args.keep_days * 86400
    removed_bytes = 0
    removed_dirs = 0
    skipped_active = 0
    records: list[dict] = []

    for user_dir in sorted(users_dir.iterdir()):
        threads = user_dir / "threads"
        if not threads.is_dir():
            continue
        for workspace in sorted(threads.iterdir()):
            if not workspace.is_dir():
                continue
            idle = now - newest_mtime(workspace)
            if idle < cutoff:
                skipped_active += 1
                continue
            for target in find_regenerable(workspace):
                size = dir_size(target)
                removed_bytes += size
                removed_dirs += 1
                records.append(
                    {
                        "path": str(target),
                        "bytes": size,
                        "idle_days": round(idle / 86400, 1),
                    }
                )
                if not args.dry_run:
                    shutil.rmtree(target, ignore_errors=True)

    if args.manifest and records:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        with args.manifest.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")

    summary = {
        "removed_dirs": removed_dirs,
        "removed_bytes": removed_bytes,
        "skipped_active_workspaces": skipped_active,
        "keep_days": args.keep_days,
        "dry_run": args.dry_run,
    }
    if args.json:
        print(json.dumps(summary))
    else:
        verb = "would remove" if args.dry_run else "removed"
        print(f"{verb} {removed_dirs} regenerable dirs, {removed_bytes / 2**30:.2f} GB; {skipped_active} active workspaces untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
