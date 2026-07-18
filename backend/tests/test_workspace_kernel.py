"""Unit tests for the Workspace Intelligence Kernel.

Phase C9 — verifies:
- All workspace models are well-formed
- Detectors classify the backend correctly
- BoundedWalker respects TraversalLimit
- SymbolIndex inverts correctly
- DependencyGraph cycle detection
- CommandRegistry lookup
- WorkspacePlanner builds valid plans
- RiskAnalyzer classifies step risk
- PlanValidator rejects invalid plans
- WIKMetrics records correctly
- WorkspaceIntelligenceServiceImpl integration
"""

from __future__ import annotations

import tempfile
from collections import defaultdict
from pathlib import Path

import pytest

from deerflow.workspace.detectors import (
    CommandDetector,
    FingerprintDetector,
    LanguageDetector,
    ProjectDetector,
)
from deerflow.workspace.graph import (
    CommandRegistry,
    DependencyGraph,
    SymbolIndex,
    WorkspaceGraph,
)
from deerflow.workspace.metrics import WIKMetrics
from deerflow.workspace.models.command import Command, CommandKind
from deerflow.workspace.models.dependency import Dependency, DepKind
from deerflow.workspace.models.execution_plan import ExecutionPlan, ExecutionStep, RiskLevel, StepKind
from deerflow.workspace.models.fingerprint import RepoKind
from deerflow.workspace.models.node import Edge, EdgeKind, Node, NodeKind
from deerflow.workspace.models.project import Project, ProjectKind
from deerflow.workspace.models.symbol import Symbol, SymbolKind, SymbolLocation
from deerflow.workspace.parsers import JSParser, PythonParser
from deerflow.workspace.planner import PlanValidator, RiskAnalyzer, WorkspacePlanner
from deerflow.workspace.scanners import BoundedWalker, TraversalLimit


# =====================================================================
# Models
# =====================================================================


class TestNodeModel:
    def test_node_kind_values(self):
        assert NodeKind.FILE in NodeKind
        assert NodeKind.PROJECT in NodeKind
        assert NodeKind.COMMAND in NodeKind

    def test_edge_kind_values(self):
        assert EdgeKind.DEPENDS_ON in EdgeKind
        assert EdgeKind.CONTAINS in EdgeKind

    def test_node_creation(self):
        node = Node(
            node_id="n1",
            kind=NodeKind.FILE,
            label="test.py",
            path="/src/test.py",
        )
        assert node.node_id == "n1"
        assert node.kind == NodeKind.FILE
        assert node.label == "test.py"


class TestDependencyModel:
    def test_dep_kind_values(self):
        assert DepKind.RUNTIME in DepKind
        assert DepKind.BUILD in DepKind
        assert DepKind.DEV in DepKind

    def test_dependency_creation(self):
        dep = Dependency(
            dependency_id="d1",
            from_project_id="p1",
            to_package="requests",
            kind=DepKind.RUNTIME,
            version_constraint=">=2.0",
        )
        assert dep.kind == DepKind.RUNTIME
        assert dep.version_constraint == ">=2.0"


# =====================================================================
# Detectors
# =====================================================================


class TestFingerprintDetector:
    def test_detect_backend(self, tmp_path):
        (tmp_path / "setup.py").write_text("# setup.py")
        detector = FingerprintDetector()
        fp = detector.detect(tmp_path)
        assert fp.kind in RepoKind

    def test_detect_empty_dir(self, tmp_path):
        detector = FingerprintDetector()
        fp = detector.detect(tmp_path)
        assert fp.kind == RepoKind.EMPTY


class TestProjectDetector:
    def test_find_python_project(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'")
        detector = ProjectDetector()
        projects = detector.find_all(tmp_path)
        assert len(projects) >= 1

    def test_find_nested_projects(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'root'")
        sub = tmp_path / "packages" / "core"
        sub.mkdir(parents=True)
        (sub / "pyproject.toml").write_text("[project]\nname = 'core'")
        detector = ProjectDetector()
        projects = detector.find_all(tmp_path)
        assert len(projects) >= 2


class TestLanguageDetector:
    def test_detect_python_majority(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo(): pass")
        (tmp_path / "b.py").write_text("def bar(): pass")
        (tmp_path / "c.js").write_text("console.log(1)")
        detector = LanguageDetector()
        result = detector.detect(tmp_path)
        assert len(result) >= 1
        assert result[0].language == "python"
        assert result[0].confidence > 0.5


class TestCommandDetector:
    def test_commands_from_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[project.scripts]\ntest = 'pytest'"
        )
        project = Project(
            project_id="p1",
            name="test",
            kind=ProjectKind.PYTHON_PROJECT,
            root_path=str(tmp_path),
            has_tests=True,
        )
        detector = CommandDetector()
        commands = detector.build_registry([project])
        names = {c.name for c in commands}
        assert "test" in names
        # migrate must NOT be fabricated for projects without alembic
        assert "migrate" not in names


# =====================================================================
# Parsers
# =====================================================================


class TestPythonParser:
    def test_parse_function(self):
        source = "def foo(x, y):\n    return x + y\n"
        parser = PythonParser()
        syms = parser.parse_source(source, "test.py")
        names = [s.name for s in syms]
        assert "foo" in names

    def test_parse_class(self):
        source = "class Bar:\n    def method(self): pass\n"
        parser = PythonParser()
        syms = parser.parse_source(source, "test.py")
        names = [s.name for s in syms]
        assert "Bar" in names

    def test_parse_invalid_syntax(self):
        parser = PythonParser()
        syms = parser.parse_source("def broken: return 1", "broken.py")
        assert isinstance(syms, list)

    def test_extract_imports(self):
        source = "import os\nfrom pathlib import Path\nimport json as j"
        parser = PythonParser()
        imports = parser.extract_imports(source)
        assert "os" in imports
        assert "pathlib" in imports


class TestJSParser:
    def test_parse_function(self):
        source = "function foo(x) { return x; }\n"
        parser = JSParser()
        syms = parser.parse_source(source, "test.js")
        names = [s.name for s in syms]
        assert "foo" in names

    def test_extract_imports(self):
        source = "import { foo } from 'bar';\nimport React from 'react';"
        parser = JSParser()
        imports = parser.extract_imports(source)
        assert "bar" in imports
        assert "react" in imports


# =====================================================================
# Graph
# =====================================================================


class TestSymbolIndex:
    def test_add_and_find(self):
        idx = SymbolIndex()
        sym = Symbol(
            symbol_id="s1",
            project_id="p1",
            name="foo",
            kind=SymbolKind.FUNCTION,
            fqn="mymodule.foo",
            file_path="/src/mymodule.py",
            language="python",
        )
        idx.add(sym)
        results = idx.find_exact("foo")
        assert len(results) == 1
        assert results[0].fqn == "mymodule.foo"

    def test_find_prefix(self):
        idx = SymbolIndex()
        for name in ["foo_bar", "foo_baz", "qux"]:
            idx.add(Symbol(
                symbol_id=name,
                project_id="p1",
                name=name,
                kind=SymbolKind.FUNCTION,
                fqn=f"m.{name}",
                file_path="/src/m.py",
                language="python",
            ))
        results = idx.find_prefix("foo_")
        assert len(results) == 2

    def test_count(self):
        idx = SymbolIndex()
        assert idx.count() == 0
        idx.add(Symbol(
            symbol_id="s1",
            project_id="p1",
            name="foo",
            kind=SymbolKind.FUNCTION,
            fqn="m.foo",
            file_path="/src/m.py",
            language="python",
        ))
        assert idx.count() == 1


class TestDependencyGraph:
    def test_add_dep(self):
        g = DependencyGraph()
        g.add_dep("/a.py", "/b.py")
        assert "/a.py" in g.dependents_of("/b.py")

    def test_topological_sort(self):
        g = DependencyGraph()
        g.add_dep("/a.py", "/b.py")
        g.add_dep("/b.py", "/c.py")
        order = g.topological_sort()
        assert order.index("/a.py") < order.index("/b.py")
        assert order.index("/b.py") < order.index("/c.py")

    def test_find_cycles(self):
        g = DependencyGraph()
        g.add_dep("/a.py", "/b.py")
        g.add_dep("/b.py", "/c.py")
        g.add_dep("/c.py", "/a.py")
        cycles = g.find_cycles()
        assert len(cycles) > 0

    def test_no_cycles(self):
        g = DependencyGraph()
        g.add_dep("/a.py", "/b.py")
        g.add_dep("/b.py", "/c.py")
        cycles = g.find_cycles()
        assert len(cycles) == 0


class TestCommandRegistry:
    def test_register_and_find(self):
        reg = CommandRegistry()
        cmd = Command(
            command_id="c1",
            name="test",
            kind=CommandKind.TEST,
            project_id="p1",
            argv=("pytest",),
            cwd="/src",
        )
        reg.register(cmd)
        found = reg.find_by_name("test")
        assert len(found) == 1
        assert found[0].argv == ("pytest",)

    def test_find_by_kind(self):
        reg = CommandRegistry()
        reg.register(Command(
            command_id="c1", name="test", kind=CommandKind.TEST,
            project_id="p1", argv=("pytest",), cwd="/src",
        ))
        reg.register(Command(
            command_id="c2", name="build", kind=CommandKind.BUILD,
            project_id="p1", argv=("build",), cwd="/src",
        ))
        test_cmds = reg.find_by_kind(CommandKind.TEST)
        assert len(test_cmds) == 1


# =====================================================================
# Scanner
# =====================================================================


class TestBoundedWalker:
    def test_respects_max_depth(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "b").mkdir()
        (tmp_path / "a" / "b" / "c").mkdir()
        limit = TraversalLimit(max_depth=2, max_files=1000, max_duration_seconds=5.0)
        walker = BoundedWalker(root=tmp_path, limits=limit)
        entries = list(walker.walk())
        depths = [e.depth for e in entries]
        assert max(depths) <= 2

    def test_respects_max_files(self, tmp_path):
        for i in range(10):
            (tmp_path / f"file{i}.py").write_text(f"# file {i}")
        limit = TraversalLimit(max_depth=10, max_files=3, max_duration_seconds=5.0)
        walker = BoundedWalker(root=tmp_path, limits=limit)
        entries = list(walker.walk())
        assert walker.stats.files_visited <= 3

    def test_empty_dir(self, tmp_path):
        limit = TraversalLimit(max_depth=6, max_files=1000, max_duration_seconds=5.0)
        walker = BoundedWalker(root=tmp_path, limits=limit)
        entries = list(walker.walk())
        assert walker.stats.files_visited == 0


# =====================================================================
# Planner
# =====================================================================


class TestRiskAnalyzer:
    def test_low_risk_read(self):
        analyzer = RiskAnalyzer()
        step = ExecutionStep(
            step_id="s1",
            kind=StepKind.READ,
            description="Read file",
            risk_level=RiskLevel.LOW,
        )
        assert analyzer.analyze_step(step) == RiskLevel.LOW

    def test_high_risk_delete(self):
        analyzer = RiskAnalyzer()
        step = ExecutionStep(
            step_id="s1",
            kind=StepKind.DELETE,
            description="Delete file",
            risk_level=RiskLevel.HIGH,
            is_destructive=True,
        )
        assert analyzer.analyze_step(step) == RiskLevel.HIGH

    def test_approve_low_plan(self):
        analyzer = RiskAnalyzer()
        plan = ExecutionPlan(
            plan_id="p1",
            goal="Read files",
            steps=(
                ExecutionStep(
                    step_id="s1", kind=StepKind.READ,
                    description="Read", risk_level=RiskLevel.LOW,
                ),
            ),
        )
        assert analyzer.approve_plan(plan) is True


class TestPlanValidator:
    def test_valid_plan(self):
        validator = PlanValidator()
        plan = ExecutionPlan(
            plan_id="p1",
            goal="Read files",
            steps=(
                ExecutionStep(
                    step_id="s1", kind=StepKind.READ,
                    description="Read file", risk_level=RiskLevel.LOW,
                ),
            ),
        )
        result = validator.validate(plan)
        assert result.is_valid is True

    def test_empty_plan(self):
        validator = PlanValidator()
        plan = ExecutionPlan(plan_id="p1", goal="Empty", steps=())
        result = validator.validate(plan)
        assert result.is_valid is False
        assert "empty" in result.errors[0].lower()

    def test_too_many_steps(self):
        validator = PlanValidator()
        steps = [
            ExecutionStep(
                step_id=f"s{i}",
                kind=StepKind.READ,
                description=f"Step {i}",
                risk_level=RiskLevel.LOW,
            )
            for i in range(51)
        ]
        plan = ExecutionPlan(plan_id="p1", goal="Too many", steps=tuple(steps))
        result = validator.validate(plan)
        assert result.is_valid is False


class TestWorkspacePlanner:
    def test_plan_read_file(self):
        planner = WorkspacePlanner(
            graph=WorkspaceGraph(),
            symbols=SymbolIndex(),
            deps=DependencyGraph(),
            commands=CommandRegistry(),
        )
        result = planner.plan_read_file("/src/test.py")
        assert result.plan is not None
        assert result.validation.is_valid is True
        assert len(result.plan.steps) == 1

    def test_plan_search(self):
        planner = WorkspacePlanner(
            graph=WorkspaceGraph(),
            symbols=SymbolIndex(),
            deps=DependencyGraph(),
            commands=CommandRegistry(),
        )
        result = planner.plan_search("def foo")
        assert result.plan is not None
        assert result.plan.steps[0].kind == StepKind.RUN_COMMAND


# =====================================================================
# Metrics
# =====================================================================


class TestWIKMetrics:
    def test_record_scan(self):
        metrics = WIKMetrics()
        metrics.record_scan(file_count=100, project_count=2, symbol_count=500, duration_ms=500.0)
        snap = metrics.snapshot()
        assert snap["scan"]["count"] == 1
        assert snap["scan"]["avg_files"] == 100.0
        assert snap["scan"]["avg_symbols"] == 500.0

    def test_record_cache_hit_miss(self):
        metrics = WIKMetrics()
        metrics.record_cache_hit()
        metrics.record_cache_hit()
        metrics.record_cache_miss()
        snap = metrics.snapshot()
        assert snap["cache"]["hits"] == 2
        assert snap["cache"]["misses"] == 1
        assert snap["cache"]["hit_rate"] == pytest.approx(2 / 3)

    def test_record_plan_built(self):
        metrics = WIKMetrics()
        metrics.record_plan_built(step_count=3, is_valid=True)
        metrics.record_plan_built(step_count=5, is_valid=False)
        snap = metrics.snapshot()
        assert snap["plan"]["count"] == 2
        assert snap["plan"]["valid"] == 1
        assert snap["plan"]["invalid"] == 1


# =====================================================================
# Events
# =====================================================================


class TestWIKEvents:
    def test_workspace_scanned_event(self):
        from deerflow.workspace.events import WorkspaceScanned
        e = WorkspaceScanned(
            root_path="/src",
            file_count=100,
            project_count=2,
            symbol_count=500,
            scan_duration_ms=300.0,
        )
        assert e.root_path == "/src"
        assert e.file_count == 100

    def test_plan_built_event(self):
        from deerflow.workspace.events import PlanBuilt
        e = PlanBuilt(
            root_path="/src",
            plan_id="p1",
            plan_title="Test plan",
            step_count=3,
            plan_valid=True,
            risk_level="LOW",
        )
        assert e.plan_valid is True
        assert e.step_count == 3

    def test_cache_events(self):
        from deerflow.workspace.events import CacheHit, CacheMiss
        hit = CacheHit(root_path="/src", cache_key="abc", hit_count=5)
        miss = CacheMiss(root_path="/src", cache_key="xyz")
        assert hit.hit_count == 5
        assert miss.cache_key == "xyz"


# =====================================================================
# Service integration
# =====================================================================


class TestWorkspaceIntelligenceServiceImpl:
    def test_service_has_required_methods(self):
        from deerflow.services.implementations import WorkspaceIntelligenceServiceImpl
        svc = WorkspaceIntelligenceServiceImpl()
        assert hasattr(svc, "scan")
        assert hasattr(svc, "plan_search")
        assert hasattr(svc, "plan_edit")
        assert hasattr(svc, "plan_run")
        assert hasattr(svc, "execute_plan")
        assert hasattr(svc, "get_snapshot")

    def test_scan_increments_metrics(self, tmp_path):
        (tmp_path / "test.py").write_text("def foo(): pass\n")
        from deerflow.services.implementations import WorkspaceIntelligenceServiceImpl
        svc = WorkspaceIntelligenceServiceImpl()
        snap = svc.scan(str(tmp_path))
        assert snap.traversal_count >= 1
        snap_metrics = svc._metrics.snapshot()
        assert snap_metrics["scan"]["count"] == 1

    def test_get_snapshot_after_scan(self, tmp_path):
        (tmp_path / "main.py").write_text("x = 1\n")
        from deerflow.services.implementations import WorkspaceIntelligenceServiceImpl
        svc = WorkspaceIntelligenceServiceImpl()
        svc.scan(str(tmp_path))
        snap = svc.get_snapshot(str(tmp_path))
        assert snap is not None
        assert snap.traversal_count >= 1

    def test_plan_search_records_metric(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo(): pass\n")
        from deerflow.services.implementations import WorkspaceIntelligenceServiceImpl
        svc = WorkspaceIntelligenceServiceImpl()
        svc.scan(str(tmp_path))
        result = svc.plan_search("def foo")
        assert result.plan is not None
        plan_metrics = svc._metrics.snapshot()
        assert plan_metrics["plan"]["count"] == 1

    def test_plan_edit_valid(self, tmp_path):
        (tmp_path / "edit_me.py").write_text("x = 1\n")
        from deerflow.services.implementations import WorkspaceIntelligenceServiceImpl
        svc = WorkspaceIntelligenceServiceImpl()
        svc.scan(str(tmp_path))
        result = svc.plan_edit(
            file_path=str(tmp_path / "edit_me.py"),
            old_string="x = 1",
            new_string="x = 2",
        )
        assert result.plan is not None
        assert result.validation.is_valid is True
