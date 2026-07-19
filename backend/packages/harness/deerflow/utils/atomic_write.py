"""Atomic text-file writes — temp file + ``os.replace()``.

A plain ``open(path, "w")`` truncates the file before any new content is
written; if the writer crashes, raises, or is killed between the truncate
and the final byte, every reader sees an empty or partially-written file
until someone notices and repairs it by hand. For files that are read on
every request (``extensions_config.json``) or represent the only copy of
user-authored state (an agent's ``config.yaml``), that's a real outage,
not a cosmetic bug.

``os.replace()`` (POSIX ``rename(2)``) is atomic at the filesystem level:
a reader always sees either the old file or the fully-written new one,
never a partial write. This is the same tempfile-then-replace pattern
already used by ``ChannelStore``, ``ChannelRuntimeConfigStore``,
``DeerFlowClient._atomic_write_json``, ``MemoryStorage``, and
``save_runtime_model_dicts`` — centralized here so new call sites reuse
it instead of re-deriving (and potentially getting wrong) the same
try/except/cleanup boilerplate.
"""

from __future__ import annotations

import tempfile
from pathlib import Path


def atomic_write_text(path: Path | str, content: str, *, mode: int | None = None) -> None:
    """Write *content* to *path* atomically.

    On any failure the original file (if it existed) is left untouched
    and the temp file is cleaned up — never a partially-written *path*.

    Args:
        path: Destination file path. Parent directory must already exist.
        content: Full text content to write (caller serializes first —
            ``json.dumps(...)``, ``yaml.dump(...)``, etc.).
        mode: Optional POSIX permission bits (e.g. ``0o600``) applied to
            the temp file before the atomic rename, so the final file
            never briefly exists with default (too-permissive) perms.
    """
    path = Path(path)
    fd = tempfile.NamedTemporaryFile(
        mode="w",
        dir=path.parent,
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    )
    try:
        if mode is not None:
            Path(fd.name).chmod(mode)
        fd.write(content)
        fd.close()
        Path(fd.name).replace(path)
    except Exception:
        fd.close()
        Path(fd.name).unlink(missing_ok=True)
        raise
