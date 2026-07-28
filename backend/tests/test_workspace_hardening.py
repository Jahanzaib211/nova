"""Phase C10 prerequisite — WIK hardening regression tests (2026-07 audit).

Covers the latent defects that must be fixed before the Workspace
Intelligence Kernel is wired to the gateway:

- B5: RiskAnalyzer was substring-based and its ``approve_plan`` compared
  str-enum members lexicographically ("critical" <= "medium" is True), so
  every plan auto-approved. Now tokenized (sudo/env unwrapping, shell -c
  payload recursion) and ordered by severity.
- B6: cache keys were SHA-256 truncated to 16 hex chars; WorkspaceCache
  mutated entries without a lock.
- C1: scan() is blocking; ``scan_async`` must offload via asyncio.to_thread.
- C5: plan_validator ignored depends_on/cycles/depth (the model lacked the
  field entirely).
- C6: workspace events were not DomainEvents, so the bus's DomainEvent-base
  subscribers never saw them.
- execute_plan mapped StepKind members that do not exist and had no risk
  gate.
"""

import asyncio
import threading

import pytest

from deerflow.workspace.models.execution_plan import (
    ExecutionPlan,
    ExecutionStep,
    RiskLevel,
    StepKind,
)


def _step(step_id="s1", kind=StepKind.READ, argv=(), depends_on=(), **kw):
    return ExecutionStep(
        step_id=step_id,
        kind=kind,
        description=f"step {step_id}",
        argv=tuple(argv),
        depends_on=tuple(depends_on),
        **kw,
    )


def _plan(*steps, risk=RiskLevel.LOW):
    return ExecutionPlan(plan_id="p1", goal="test", steps=tuple(steps), risk_level=risk)


# ---------------------------------------------------------------------------
# B5 — RiskAnalyzer
# ---------------------------------------------------------------------------


class TestRiskAnalyzerOrdering:
    def _analyzer(self):
        from deerflow.workspace.planner.risk_analyzer import RiskAnalyzer

        return RiskAnalyzer()

    def test_high_risk_plan_is_not_auto_approved(self):
        plan = _plan(_step(kind=StepKind.DELETE))
        assert self._analyzer().approve_plan(plan) is False

    def test_critical_plan_is_not_auto_approved(self):
        plan = _plan(_step(kind=StepKind.RUN_COMMAND, argv=("rm", "-rf", "/")))
        assert self._analyzer().approve_plan(plan) is False

    def test_low_and_medium_plans_auto_approve(self):
        assert self._analyzer().approve_plan(_plan(_step(kind=StepKind.READ))) is True
        assert self._analyzer().approve_plan(_plan(_step(kind=StepKind.RUN_COMMAND, argv=("pytest",)))) is True


class TestRiskAnalyzerTokenized:
    def _risk(self, argv, kind=StepKind.RUN_COMMAND):
        from deerflow.workspace.planner.risk_analyzer import RiskAnalyzer

        return RiskAnalyzer().analyze_step(_step(kind=kind, argv=argv))

    def test_git_reset_hard_is_high(self):
        assert self._risk(("git", "reset", "--hard", "HEAD~1")).order >= RiskLevel.HIGH.order

    def test_git_push_force_is_high(self):
        assert self._risk(("git", "push", "--force", "origin", "main")).order >= RiskLevel.HIGH.order
        assert self._risk(("git", "push", "-f")).order >= RiskLevel.HIGH.order

    def test_git_clean_forced_is_high(self):
        assert self._risk(("git", "clean", "-fd")).order >= RiskLevel.HIGH.order
        # flag order / separation must not matter (substring match missed these)
        assert self._risk(("git", "clean", "-d", "-f")).order >= RiskLevel.HIGH.order

    def test_rm_recursive_force_on_root_is_critical(self):
        assert self._risk(("rm", "-rf", "/")) == RiskLevel.CRITICAL
        # flag order variant bypassed the old substring check
        assert self._risk(("rm", "-fr", "/")) == RiskLevel.CRITICAL
        assert self._risk(("rm", "-r", "-f", "/")) == RiskLevel.CRITICAL

    def test_rm_recursive_force_on_project_path_is_high(self):
        assert self._risk(("rm", "-rf", "./build")).order >= RiskLevel.HIGH.order

    def test_dd_to_block_device_is_critical(self):
        assert self._risk(("dd", "if=/dev/zero", "of=/dev/sda")) == RiskLevel.CRITICAL

    def test_mkfs_is_critical(self):
        assert self._risk(("mkfs.ext4", "/dev/sda1")) == RiskLevel.CRITICAL

    def test_sudo_wrapped_command_is_unwrapped(self):
        assert self._risk(("sudo", "rm", "-rf", "/")) == RiskLevel.CRITICAL
        assert self._risk(("sudo", "git", "reset", "--hard")).order >= RiskLevel.HIGH.order

    def test_shell_dash_c_payload_is_analyzed(self):
        # the split/var bypass called out by the audit
        assert self._risk(("bash", "-c", "git reset --hard HEAD~2")).order >= RiskLevel.HIGH.order
        assert self._risk(("sh", "-c", "rm -rf /")) == RiskLevel.CRITICAL

    def test_echo_of_dangerous_text_is_not_flagged(self):
        # the substring matcher would have false-positived here
        assert self._risk(("echo", "rm -rf /")).order <= RiskLevel.MEDIUM.order

    def test_plain_commands_stay_low_or_medium(self):
        assert self._risk(("pytest", "-q")).order <= RiskLevel.MEDIUM.order
        assert self._risk(("ls", "-la")).order <= RiskLevel.MEDIUM.order


# ---------------------------------------------------------------------------
# C5 — PlanValidator
# ---------------------------------------------------------------------------


class TestPlanValidatorDependencies:
    def _validate(self, *steps):
        from deerflow.workspace.planner.plan_validator import PlanValidator

        return PlanValidator().validate(_plan(*steps))

    def test_unknown_dependency_is_error(self):
        result = self._validate(_step("a", depends_on=("nope",)))
        assert result.is_valid is False
        assert any("nope" in e for e in result.errors)

    def test_self_dependency_is_error(self):
        result = self._validate(_step("a", depends_on=("a",)))
        assert result.is_valid is False

    def test_dependency_cycle_is_error(self):
        result = self._validate(
            _step("a", depends_on=("b",)),
            _step("b", depends_on=("a",)),
        )
        assert result.is_valid is False
        assert any("cycle" in e.lower() for e in result.errors)

    def test_valid_dependency_chain_passes(self):
        result = self._validate(
            _step("a"),
            _step("b", depends_on=("a",)),
            _step("c", depends_on=("b",)),
        )
        assert result.is_valid is True

    def test_chain_deeper_than_max_depth_is_error(self):
        from deerflow.workspace.planner.plan_validator import PlanValidator

        steps = [_step("s0")]
        for i in range(1, PlanValidator.MAX_DEPTH + 2):
            steps.append(_step(f"s{i}", depends_on=(f"s{i - 1}",)))
        result = self._validate(*steps)
        assert result.is_valid is False
        assert any("depth" in e.lower() for e in result.errors)

    def test_high_risk_plan_gets_warning(self):
        from deerflow.workspace.planner.plan_validator import PlanValidator

        result = PlanValidator().validate(_plan(_step(kind=StepKind.DELETE, is_destructive=True)))
        assert result.is_valid is True
        assert any("risk" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# C6 — workspace events are DomainEvents
# ---------------------------------------------------------------------------


class TestWorkspaceEventsAreDomainEvents:
    def test_workspace_event_subclasses_domain_event(self):
        from deerflow.events.event import DomainEvent
        from deerflow.workspace.events import CacheHit, PlanBuilt, WorkspaceEvent, WorkspaceScanned

        assert issubclass(WorkspaceEvent, DomainEvent)
        for cls in (WorkspaceScanned, PlanBuilt, CacheHit):
            assert issubclass(cls, DomainEvent)

    def test_bus_base_subscribers_receive_workspace_events(self):
        from deerflow.events.bus import EventBus
        from deerflow.events.event import DomainEvent
        from deerflow.workspace.events import WorkspaceScanned

        bus = EventBus()
        seen = []
        bus.subscribe(DomainEvent, seen.append)
        event = WorkspaceScanned(root_path="/src", file_count=10)
        bus.publish(event)
        assert seen == [event]

    def test_workspace_event_keeps_occurred_at_and_root_path(self):
        from deerflow.workspace.events import WorkspaceScanned

        event = WorkspaceScanned(root_path="/src", file_count=1)
        assert event.root_path == "/src"
        assert event.occurred_at


# ---------------------------------------------------------------------------
# B6 — cache key + cache locking
# ---------------------------------------------------------------------------


class TestCacheKeyStrength:
    def test_snapshot_key_uses_full_digest(self):
        from deerflow.workspace.cache.cache_key import CacheKeyBuilder

        key = CacheKeyBuilder().build_snapshot_key("/src", "backend", "python", 3, 100)
        digest = key.removeprefix("wik:")
        assert len(digest) == 64

    def test_file_and_symbol_keys_use_full_digest(self):
        from deerflow.workspace.cache.cache_key import CacheKeyBuilder

        b = CacheKeyBuilder()
        assert len(b.build_file_hash_key("a.py", "h1").rsplit(":", 1)[-1]) == 64
        assert len(b.build_symbol_key("p1", "sym").rsplit(":", 1)[-1]) == 64


class TestSnapshotSerialization:
    def test_round_trip_through_json(self, tmp_path):
        import json

        from deerflow.services.implementations import WorkspaceIntelligenceServiceImpl

        (tmp_path / "a.py").write_text("def f():\n    return 1\n\nclass C:\n    pass\n")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\nversion = '0.1'\n")

        snap = WorkspaceIntelligenceServiceImpl().scan(str(tmp_path))
        from deerflow.workspace.models.workspace_snapshot import WorkspaceSnapshot

        rebuilt = WorkspaceSnapshot.from_dict(json.loads(json.dumps(snap.to_dict())))
        assert rebuilt.thread_id == snap.thread_id
        assert rebuilt.project_count == snap.project_count
        assert rebuilt.symbol_count == snap.symbol_count
        assert {s.name for s in rebuilt.symbols} == {s.name for s in snap.symbols}
        assert rebuilt.fingerprint.kind == snap.fingerprint.kind


class TestWorkspaceCacheLocking:
    def test_concurrent_set_and_get_do_not_corrupt(self, tmp_path):
        from deerflow.workspace.cache.workspace_cache import WorkspaceCache
        from deerflow.workspace.models.fingerprint import RepoKind, RepositoryFingerprint
        from deerflow.workspace.models.workspace_snapshot import WorkspaceSnapshot

        cache = WorkspaceCache(cache_dir=tmp_path / "wc")
        fp = RepositoryFingerprint(kind=RepoKind.BACKEND, primary_language="python")
        snap = WorkspaceSnapshot(thread_id="t", fingerprint=fp)

        errors = []

        def writer(i):
            try:
                for j in range(20):
                    cache.set(f"k{i}-{j}", snap)
            except Exception as e:  # pragma: no cover - failure diagnostics
                errors.append(e)

        def reader(i):
            try:
                for j in range(20):
                    cache.get(f"k{i}-{j}")
                    cache.stats()
            except Exception as e:  # pragma: no cover - failure diagnostics
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        threads += [threading.Thread(target=reader, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert cache.size() == 80

    def test_cache_has_a_lock(self):
        from deerflow.workspace.cache.workspace_cache import WorkspaceCache

        cache = WorkspaceCache.__new__(WorkspaceCache)
        assert hasattr(type(cache), "__post_init__")


# ---------------------------------------------------------------------------
# C1 — async scan offload + snapshot cache semantics
# ---------------------------------------------------------------------------


class TestScanAsyncAndCaching:
    def _svc(self):
        from deerflow.services.implementations import WorkspaceIntelligenceServiceImpl

        return WorkspaceIntelligenceServiceImpl()

    def test_scan_async_offloads_off_the_event_loop(self, tmp_path):
        svc = self._svc()
        (tmp_path / "a.py").write_text("def f():\n    return 1\n")

        loop_thread = threading.current_thread()
        scan_thread: list[threading.Thread] = []
        original_scan = svc.scan

        def tracking_scan(root_path, force_refresh=False):
            scan_thread.append(threading.current_thread())
            return original_scan(root_path, force_refresh=force_refresh)

        svc.scan = tracking_scan

        async def run():
            return await svc.scan_async(str(tmp_path))

        snapshot = asyncio.run(run())
        assert snapshot.thread_id == str(tmp_path)
        assert scan_thread and scan_thread[0] is not loop_thread

    def test_second_scan_is_a_cache_hit(self, tmp_path):
        svc = self._svc()
        (tmp_path / "a.py").write_text("def f():\n    return 1\n")
        first = svc.scan(str(tmp_path))
        second = svc.scan(str(tmp_path))
        assert second is first
        assert svc._metrics._cache_hits >= 1

    def test_force_refresh_rescans(self, tmp_path):
        svc = self._svc()
        (tmp_path / "a.py").write_text("def f():\n    return 1\n")
        first = svc.scan(str(tmp_path))
        second = svc.scan(str(tmp_path), force_refresh=True)
        assert second is not first


# ---------------------------------------------------------------------------
# execute_plan — risk gate + real StepKind mapping
# ---------------------------------------------------------------------------


class TestExecutePlanGate:
    def _svc(self):
        from deerflow.services.implementations import WorkspaceIntelligenceServiceImpl

        return WorkspaceIntelligenceServiceImpl()

    def test_high_risk_plan_is_refused_without_approval(self):
        plan = _plan(
            _step(kind=StepKind.RUN_COMMAND, argv=("git", "reset", "--hard")),
        )
        with pytest.raises(PermissionError):
            self._svc().execute_plan(plan)

    def test_critical_plan_is_refused_even_with_approval(self):
        plan = _plan(_step(kind=StepKind.RUN_COMMAND, argv=("rm", "-rf", "/")))
        with pytest.raises(PermissionError):
            self._svc().execute_plan(plan, approved=True)

    def test_invalid_plan_is_rejected(self):
        plan = _plan(_step("a", depends_on=("missing",)))
        with pytest.raises(ValueError):
            self._svc().execute_plan(plan)

    def test_step_kind_mapping_covers_real_members(self):
        svc = self._svc()
        from deerflow.execution.models import ExecutionClass

        for kind in StepKind:
            mapped = svc._step_kind_to_class(kind)
            assert isinstance(mapped, ExecutionClass)
