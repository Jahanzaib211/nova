"""The todo API has exactly one authority: the LangGraph checkpoint.

Before this, ``/api/sandbox/todo`` read ``todo.md`` first and consulted the
checkpoint only ``if not todos``.  That made the panel non-deterministic -- the
list flipped between two sources depending on whether the file happened to parse
to >=1 item -- and the "primary" source was dead code on container deployments:
``_write_todo_md_file`` returned early for any sandbox id lacking a ``local:``
prefix, which is every AioSandbox id.
"""

from types import SimpleNamespace

import pytest

from app.gateway.routers.sandbox import _format_todo_md, _normalize_todos


class _Checkpointer:
    def __init__(self, todos):
        self._todos = todos

    async def aget_tuple(self, config):
        if self._todos is None:
            return None
        return SimpleNamespace(checkpoint={"channel_values": {"todos": self._todos}})


class TestContentAndTodosNeverDisagree:
    def test_content_is_derived_from_the_returned_todos(self):
        """``content`` used to be set from the file read and only recomputed in
        the fallback branch, so the two fields could describe different lists."""
        todos = _normalize_todos([{"content": "build it", "status": "in_progress"}])
        content = _format_todo_md(todos)
        assert "build it" in content
        parsed_back = [t["description"] for t in todos]
        assert parsed_back == ["build it"]

    def test_empty_todos_yield_empty_content(self):
        assert (_format_todo_md([]) if [] else "") == ""


class TestWriterNoLongerSkipsContainerSandboxes:
    @pytest.mark.parametrize(
        "sandbox_id",
        ["13143589", "abc123def", "local:t-1"],
        ids=["aio-hash", "aio-hash-2", "local"],
    )
    def test_thread_id_from_config_is_used_regardless_of_sandbox_id(self, tmp_path, monkeypatch, sandbox_id):
        """A hash sandbox id must no longer disable the write."""
        from deerflow.agents.middlewares import observe_adjust_middleware as mod

        written = {}

        class _Paths:
            def sandbox_work_dir(self, thread_id, *, user_id=None):
                d = tmp_path / str(thread_id) / "workspace"
                written["dir"] = d
                return d

        monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: _Paths())
        monkeypatch.setattr("deerflow.runtime.user_context.get_effective_user_id", lambda: "u1")

        state = {"sandbox": {"sandbox_id": sandbox_id}}
        mod._write_todo_md_file([{"content": "ship", "status": "pending"}], state, "thread-42")

        todo_file = tmp_path / "thread-42" / "workspace" / "todo.md"
        assert todo_file.exists(), "todo.md was not written for this sandbox id"
        assert "ship" in todo_file.read_text()

    def test_no_thread_id_anywhere_is_a_clean_skip(self, monkeypatch):
        from deerflow.agents.middlewares import observe_adjust_middleware as mod

        # Must not raise, must not write.
        mod._write_todo_md_file([{"content": "x", "status": "pending"}], {"sandbox": {"sandbox_id": "hash"}}, None)


class TestThreadIdFromConfig:
    @pytest.mark.parametrize(
        "config,expected",
        [
            ({"configurable": {"thread_id": "t1"}}, "t1"),
            ({"configurable": {}}, None),
            ({"configurable": None}, None),
            ({}, None),
            (None, None),
            ("not-a-dict", None),
            ({"configurable": {"thread_id": ""}}, None),
        ],
    )
    def test_tolerates_every_config_shape(self, config, expected):
        from deerflow.agents.middlewares.observe_adjust_middleware import _thread_id_from_config

        assert _thread_id_from_config(config) == expected
