"""Smoke test for the screenshot tool's data-URI result handling.

The screenshot tool returns ``data:image/png;base64,...``. We don't render the
full ToolCall component here (it has heavy deps and lives inside ChainOfThought);
instead we assert the parsing + branching logic via a tiny pure-function helper
mirroring the branch in message-group.tsx.

The branch logic is the part most likely to regress when the result format
changes (e.g. adding a metadata envelope), so we keep it pinned.
"""

from __future__ import annotations

import base64

import pytest


def parse_screenshot_result(result: str | dict | None) -> dict:
    """Mirror of the message-group.tsx screenshot branch.

    Returns a dict with:
      - ``src``: the data: URI if result is a valid PNG inline payload.
      - ``kind``: "image" | "text" | "missing".
    """
    if not isinstance(result, str):
        return {"src": None, "kind": "missing"}
    if result.startswith("data:image/png;base64,"):
        # Validate the base64 payload to fail fast on corrupt results.
        b64 = result.split(",", 1)[1]
        try:
            base64.b64decode(b64, validate=True)
        except Exception:
            return {"src": None, "kind": "text"}
        return {"src": result, "kind": "image"}
    if result.startswith("Error:"):
        return {"src": None, "kind": "text"}
    return {"src": None, "kind": "missing"}


class TestParseScreenshotResult:
    def test_valid_png_payload(self) -> None:
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"fake-content"
        b64 = base64.b64encode(png_bytes).decode("ascii")
        result = f"data:image/png;base64,{b64}"
        out = parse_screenshot_result(result)
        assert out["kind"] == "image"
        assert out["src"] == result

    def test_error_string_falls_back_to_text(self) -> None:
        out = parse_screenshot_result("Error: screenshot unavailable")
        assert out["kind"] == "text"
        assert out["src"] is None

    def test_non_data_uri_returns_missing(self) -> None:
        # Old browser_check tool might return a non-PNG result here; the
        # screenshot branch should NOT try to render it as an image.
        out = parse_screenshot_result("console_errors: Cannot find module")
        assert out["kind"] == "missing"
        assert out["src"] is None

    def test_none_input(self) -> None:
        out = parse_screenshot_result(None)
        assert out == {"src": None, "kind": "missing"}

    def test_dict_input(self) -> None:
        out = parse_screenshot_result({"type": "image", "data": "..."})
        assert out["kind"] == "missing"

    def test_corrupt_base64(self) -> None:
        out = parse_screenshot_result("data:image/png;base64,!!!not-base64!!!")
        assert out["kind"] == "text"
        assert out["src"] is None

    def test_empty_payload(self) -> None:
        out = parse_screenshot_result("")
        assert out["kind"] == "missing"

    @pytest.mark.parametrize(
        "prefix",
        ["data:image/jpeg;base64,", "data:image/webp;base64,", "data:image/gif;base64,"],
    )
    def test_other_image_formats_not_rendered(self, prefix: str) -> None:
        """The screenshot tool is documented as PNG-only; other formats
        should fall back to text rendering rather than be silently mis-
        represented."""
        b64 = base64.b64encode(b"x").decode("ascii")
        out = parse_screenshot_result(prefix + b64)
        assert out["kind"] == "missing"


class TestScreenshotToolContract:
    """Pin the tool name and result shape so the manifest test + UI branch
    stay in sync."""

    def test_tool_name_is_screenshot(self) -> None:
        # The Python tool is named "screenshot" via @tool("screenshot").
        # The manifest entry MUST be the same name for the UI branch to fire.
        from deerflow.tools.builtins.workspace_tools import screenshot_tool

        assert screenshot_tool.name == "screenshot"

    def test_tool_registered_in_builtin_list(self) -> None:
        from deerflow.tools.tools import BUILTIN_TOOLS

        names = [t.name for t in BUILTIN_TOOLS]
        assert "screenshot" in names

    def test_manifest_describes_screenshot(self) -> None:
        # Read the manifest dict by walking the module's namespace.
        # We don't depend on the exact var name (it was renamed during
        # the v7 hardening); instead scan for any dict that contains
        # the 'screenshot' key.
        import deerflow.agents.manifest as m

        candidate = None
        for name in dir(m):
            value = getattr(m, name)
            if isinstance(value, dict) and "screenshot" in value:
                candidate = value
                break
        assert candidate is not None, "manifest dict with 'screenshot' key not found"
        assert "viewport" in candidate["screenshot"].lower()
