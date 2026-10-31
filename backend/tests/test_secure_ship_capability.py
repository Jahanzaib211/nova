"""Tests for the Secure-Ship capability module.

The gate engine itself lives in ``deerflow.security.secure_ship`` and is
tested there. What is pinned here is this module's contract with the rest of
Nova: the module registers exactly once, its four operations carry the kinds
and surfaces the registry derives tools and MCP listings from, and the
handlers refuse rather than guess when there is no thread to scan.
"""

from __future__ import annotations

import json

import pytest

from deerflow.capabilities.modules import register_builtin_modules
from deerflow.capabilities.modules import secure_ship as secure_ship_module
from deerflow.capabilities.modules._common import Empty
from deerflow.capabilities.modules.secure_ship import MODULE, ScanIn, ThreadScoped
from deerflow.capabilities.registry import CapabilityRegistry, surface_allows
from deerflow.capabilities.types import OpContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeReport:
    """The engine's report, reduced to what the module reads."""

    def __init__(self, verdict: str = "pass", counts: dict | None = None, findings: list | None = None) -> None:
        self.thread_id = "thread-1"
        self.verdict = verdict
        self.counts = counts or {}
        self.findings = findings or []
        self.duration_ms = 7

    def to_dict(self) -> dict:
        return {
            "thread_id": self.thread_id,
            "verdict": self.verdict,
            "counts": self.counts,
            "findings": self.findings,
            "duration_ms": self.duration_ms,
        }


def _ctx(*, thread_id: str | None = "thread-1", user_id: str = "u1") -> OpContext:
    return OpContext(user_id=user_id, thread_id=thread_id)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_module_registers_under_its_own_id(self):
        reg = CapabilityRegistry()
        reg.register(MODULE)
        assert MODULE.id == "secure_ship"
        assert [m.id for m in reg.modules()] == ["secure_ship"]

    def test_registering_twice_is_refused(self):
        reg = CapabilityRegistry()
        reg.register(MODULE)
        with pytest.raises(ValueError, match="already registered"):
            reg.register(MODULE)

    def test_builtin_registration_includes_it_exactly_once(self):
        reg = CapabilityRegistry()
        register_builtin_modules(reg)
        ids = [m.id for m in reg.modules()]
        assert ids.count("secure_ship") == 1

    def test_module_has_no_feature_flag(self):
        """No flag: the gate is always available (it must not need config)."""
        assert MODULE.flag is None
        reg = CapabilityRegistry()
        reg.register(MODULE)
        for op in reg.operations(enabled_flags=set()):
            assert op.flag is None


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


class TestOperations:
    EXPECTED = {
        "secure_ship.scan": "execute",
        "secure_ship.report": "read",
        "secure_ship.toolkit": "read",
        "secure_ship.policy": "read",
    }

    def _registry(self) -> CapabilityRegistry:
        reg = CapabilityRegistry()
        reg.register(MODULE)
        return reg

    def test_all_four_operations_exist(self):
        reg = self._registry()
        assert {op.name for op in reg.operations()} == set(self.EXPECTED)

    def test_kinds_are_the_declared_ones(self):
        reg = self._registry()
        for name, kind in self.EXPECTED.items():
            assert reg.get(name).kind == kind, name

    def test_operations_are_harness_and_mcp_reachable(self):
        reg = self._registry()
        for name in self.EXPECTED:
            op = reg.get(name)
            assert op.harness is True, name
            assert op.mcp is True, name

    def test_surface_allows_puts_secure_ship_on_both_surfaces(self):
        reg = self._registry()
        for name in self.EXPECTED:
            op = reg.get(name)
            assert surface_allows(op, "harness") is True, name
            assert surface_allows(op, "mcp") is True, name

    def test_input_shapes(self):
        scan = ScanIn()
        assert scan.thread_id is None
        assert scan.deep is False
        assert ScanIn(thread_id="t1", deep=True).deep is True
        assert ThreadScoped().thread_id is None

    def test_output_shapes(self):
        assert set(secure_ship_module.ScanOut.model_fields) == {"report"}
        assert set(secure_ship_module.ReportOut.model_fields) == {"report"}
        assert set(secure_ship_module.ToolkitOut.model_fields) == {"toolkit"}
        assert set(secure_ship_module.PolicyOut.model_fields) == {"policy"}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


class TestScan:
    @pytest.mark.asyncio
    async def test_scan_returns_the_report_dict(self, monkeypatch):
        calls: list[dict] = []

        async def fake_run_gate(thread_id, *, user_id=None, policy=None, deep=False):
            calls.append({"thread_id": thread_id, "user_id": user_id, "deep": deep})
            return _FakeReport(verdict="warn", counts={"medium": 1})

        monkeypatch.setattr(secure_ship_module, "run_gate", fake_run_gate)

        out = await secure_ship_module._scan(_ctx(), ScanIn())
        assert out.report["verdict"] == "warn"
        assert out.report["counts"] == {"medium": 1}
        assert calls == [{"thread_id": "thread-1", "user_id": "u1", "deep": False}]

    @pytest.mark.asyncio
    async def test_scan_passes_the_deep_flag(self, monkeypatch):
        seen: dict = {}

        async def fake_run_gate(thread_id, *, user_id=None, policy=None, deep=False):
            seen["deep"] = deep
            return _FakeReport()

        monkeypatch.setattr(secure_ship_module, "run_gate", fake_run_gate)
        await secure_ship_module._scan(_ctx(), ScanIn(deep=True))
        assert seen["deep"] is True

    @pytest.mark.asyncio
    async def test_scan_refuses_a_missing_thread(self, monkeypatch):
        async def fake_run_gate(*args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("the gate must not run without a thread")

        monkeypatch.setattr(secure_ship_module, "run_gate", fake_run_gate)
        with pytest.raises(ValueError, match="No thread in scope"):
            await secure_ship_module._scan(_ctx(thread_id=None), ScanIn())

    @pytest.mark.asyncio
    async def test_scan_prefers_an_explicit_thread(self, monkeypatch):
        seen: dict = {}

        async def fake_run_gate(thread_id, *, user_id=None, policy=None, deep=False):
            seen["thread_id"] = thread_id
            return _FakeReport()

        monkeypatch.setattr(secure_ship_module, "run_gate", fake_run_gate)
        await secure_ship_module._scan(_ctx(thread_id="ctx-thread"), ScanIn(thread_id="explicit-thread"))
        assert seen["thread_id"] == "explicit-thread"

    @pytest.mark.asyncio
    async def test_a_failing_gate_becomes_a_value_error_not_a_500(self, monkeypatch):
        async def boom(thread_id, *, user_id=None, policy=None, deep=False):
            raise RuntimeError("scanner offline")

        monkeypatch.setattr(secure_ship_module, "run_gate", boom)
        with pytest.raises(ValueError, match="Secure-Ship scan failed"):
            await secure_ship_module._scan(_ctx(), ScanIn())


class TestReport:
    @pytest.mark.asyncio
    async def test_report_is_none_when_nothing_was_written(self, monkeypatch):
        monkeypatch.setattr(secure_ship_module, "_find_report_file", lambda thread_id, user_id: None)
        out = await secure_ship_module._report(_ctx(), ThreadScoped())
        assert out.report is None

    @pytest.mark.asyncio
    async def test_report_reads_the_persisted_report(self, monkeypatch, tmp_path):
        path = tmp_path / "SECURE-SHIP.json"
        path.write_text(json.dumps({"verdict": "block", "counts": {"high": 2}}), encoding="utf-8")
        monkeypatch.setattr(secure_ship_module, "_find_report_file", lambda thread_id, user_id: path)

        out = await secure_ship_module._report(_ctx(), ThreadScoped())
        assert out.report["verdict"] == "block"
        assert out.report["counts"] == {"high": 2}

    @pytest.mark.asyncio
    async def test_report_refuses_a_missing_thread(self):
        with pytest.raises(ValueError, match="No thread in scope"):
            await secure_ship_module._report(_ctx(thread_id=None), ThreadScoped())


class TestReportDiscovery:
    """Where the persisted report is looked for."""

    def _workspace(self, tmp_path, monkeypatch):
        monkeypatch.setattr(secure_ship_module, "_workspace_dirs", lambda thread_id, user_id: [tmp_path])
        return tmp_path

    def test_the_engine_file_name_wins(self, tmp_path, monkeypatch):
        self._workspace(tmp_path, monkeypatch)
        (tmp_path / "SECURE-SHIP.json").write_text("{}", encoding="utf-8")
        (tmp_path / "other.json").write_text(json.dumps({"verdict": "pass", "findings": []}), encoding="utf-8")
        assert secure_ship_module._find_report_file("t1", None) == tmp_path / "SECURE-SHIP.json"

    def test_a_gate_shaped_json_is_found_whatever_its_name(self, tmp_path, monkeypatch):
        self._workspace(tmp_path, monkeypatch)
        (tmp_path / "build-42.json").write_text(json.dumps({"verdict": "pass", "findings": []}), encoding="utf-8")
        assert secure_ship_module._find_report_file("t1", None) == tmp_path / "build-42.json"

    def test_an_unrelated_json_is_not_a_report(self, tmp_path, monkeypatch):
        self._workspace(tmp_path, monkeypatch)
        (tmp_path / "package.json").write_text(json.dumps({"name": "app"}), encoding="utf-8")
        assert secure_ship_module._find_report_file("t1", None) is None

    def test_an_empty_workspace_has_no_report(self, tmp_path, monkeypatch):
        self._workspace(tmp_path, monkeypatch)
        assert secure_ship_module._find_report_file("t1", None) is None


class TestToolkitAndPolicy:
    @pytest.mark.asyncio
    async def test_toolkit_returns_the_manifest(self, monkeypatch):
        manifest = {"scanners": [{"id": "secrets", "description": "hard-coded secrets"}]}
        monkeypatch.setattr(secure_ship_module, "toolkit_manifest", lambda thread_id=None: manifest)

        out = await secure_ship_module._toolkit(_ctx(), Empty())
        assert out.toolkit["scanners"][0]["id"] == "secrets"

    @pytest.mark.asyncio
    async def test_toolkit_failure_is_a_value_error(self, monkeypatch):
        def boom(thread_id=None):
            raise RuntimeError("no toolkit")

        monkeypatch.setattr(secure_ship_module, "toolkit_manifest", boom)
        with pytest.raises(ValueError, match="toolkit unavailable"):
            await secure_ship_module._toolkit(_ctx(), Empty())

    @pytest.mark.asyncio
    async def test_policy_returns_the_active_policy(self, monkeypatch):
        monkeypatch.setattr(secure_ship_module, "default_policy", lambda: {"block_on": ["high"], "fail_open": False})

        out = await secure_ship_module._policy(_ctx(), Empty())
        assert out.policy["block_on"] == ["high"]
        assert out.policy["fail_open"] is False

    @pytest.mark.asyncio
    async def test_policy_failure_is_a_value_error(self, monkeypatch):
        def boom():
            raise RuntimeError("no policy")

        monkeypatch.setattr(secure_ship_module, "default_policy", boom)
        with pytest.raises(ValueError, match="policy unavailable"):
            await secure_ship_module._policy(_ctx(), Empty())
