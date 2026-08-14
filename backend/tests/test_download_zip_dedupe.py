"""The workspace zip must not contain two entries claiming the same path.

`/api/sandbox/download-zip` walks three roots and flattens the workspace to the
archive root while prefixing outputs/ and uploads/ with their mount folder. That
puts the workspace's OWN `outputs/` directory in the same namespace as the
outputs mount, so `workspace/outputs/report.md` and `outputs/report.md` both
computed `arcname="outputs/report.md"`.

`zipfile.write` accepts that — it only emits a `UserWarning: Duplicate name` —
and every extractor keeps whichever entry lands last. So one of the two files was
silently unrecoverable from a download the user believes is a complete backup.

The same walk also used `str(rel)` on the workspace branch, which renders with
the OS separator; on Windows that writes backslash-separated entry names, which
the ZIP spec forbids (entries must be `/`-separated).
"""

from __future__ import annotations

import asyncio
import io
import warnings
import zipfile
from types import SimpleNamespace

import pytest

from app.gateway.routers import sandbox as sandbox_router


@pytest.fixture
def thread_tree(tmp_path, monkeypatch):
    """Build a real on-disk user-data tree and point the router at it."""
    user_data = tmp_path / "user-data"
    workspace = user_data / "workspace"
    outputs = user_data / "outputs"
    uploads = user_data / "uploads"
    for d in (workspace, outputs, uploads):
        d.mkdir(parents=True)

    # The collision: a workspace-local outputs/ dir alongside the outputs mount.
    (workspace / "outputs").mkdir()
    (workspace / "outputs" / "report.md").write_text("FROM WORKSPACE", encoding="utf-8")
    (outputs / "report.md").write_text("FROM OUTPUTS MOUNT", encoding="utf-8")
    (workspace / "src").mkdir()
    (workspace / "src" / "index.ts").write_text("export {};", encoding="utf-8")
    (uploads / "notes.txt").write_text("hello", encoding="utf-8")

    fake_paths = SimpleNamespace(
        sandbox_work_dir=lambda tid, user_id=None: workspace,
        sandbox_outputs_dir=lambda tid, user_id=None: outputs,
        sandbox_uploads_dir=lambda tid, user_id=None: uploads,
    )
    monkeypatch.setattr(sandbox_router, "get_paths", lambda: fake_paths)
    monkeypatch.setattr(sandbox_router, "get_effective_user_id", lambda: "u1")
    monkeypatch.setattr(sandbox_router, "_caller_owns_thread", lambda tid: True)
    return tmp_path


def _zip_bytes() -> bytes:
    """Drive the endpoint and drain its StreamingResponse.

    Starlette wraps a sync iterator with ``iterate_in_threadpool``, so
    ``body_iterator`` is an async iterator even though the route passes
    ``iter([...])``.
    """

    async def run() -> bytes:
        resp = await sandbox_router.download_sandbox_zip("t1", request=None)  # type: ignore[arg-type]
        chunks = [chunk async for chunk in resp.body_iterator]
        return b"".join(bytes(c) for c in chunks)

    return asyncio.run(run())


def _zip_names() -> list[str]:
    with zipfile.ZipFile(io.BytesIO(_zip_bytes())) as zf:
        return zf.namelist()


class TestZipEntriesAreUnique:
    def test_no_duplicate_arcnames(self, thread_tree) -> None:
        names = _zip_names()
        assert len(names) == len(set(names)), f"duplicate zip entries — one file is unrecoverable: {sorted(names)}"

    def test_both_colliding_files_survive_with_distinct_content(self, thread_tree) -> None:
        """The point of the fix: neither file is lost."""
        with zipfile.ZipFile(io.BytesIO(_zip_bytes())) as zf:
            contents = {zf.read(n).decode("utf-8") for n in zf.namelist()}
        assert "FROM WORKSPACE" in contents
        assert "FROM OUTPUTS MOUNT" in contents

    def test_expected_files_are_present(self, thread_tree) -> None:
        names = _zip_names()
        assert "src/index.ts" in names
        assert "uploads/notes.txt" in names
        assert "outputs/report.md" in names

    def test_entry_names_are_posix_separated(self, thread_tree) -> None:
        """ZIP entries must use `/`; str(rel) would emit `\\` on Windows."""
        for name in _zip_names():
            assert "\\" not in name, f"non-POSIX zip entry name: {name!r}"

    def test_no_duplicate_name_warning_is_emitted(self, thread_tree) -> None:
        """zipfile only warns on a duplicate — that warning is the bug's fingerprint."""
        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            _zip_names()
        assert not [w for w in record if "Duplicate name" in str(w.message)]
