# Workspace Intelligence Kernel (Phase C9)

The Workspace Intelligence Kernel (WIK) replaces filesystem-based grep/shell exploration with a typed graph engine, symbol index, and deterministic execution planning. Every workspace query — search, read, edit, or run — begins with a plan built from indexed metadata.

## Architecture

```
Workspace scan
  → Detectors (fingerprint, project, language, command)
  → BoundedWalker (TraversalLimit: max_depth=6, max_files=50k, max_time=15s)
  → Parsers (Python ast, JS regex)
  → WorkspaceGraph + SymbolIndex + DependencyGraph + CommandRegistry
  → Cache (disk-backed JSON, SHA-256 keys)
  → WorkspacePlanner (execution plan)
  → PlanValidator + RiskAnalyzer
  → Execution Kernel (Phase C7 pipeline)
```

## Components

| Module | Responsibility |
|---|---|
| `models/` | Frozen dataclasses: `NodeKind`, `EdgeKind`, `Node`, `Edge`, `RepoKind`, `RepositoryFingerprint`, `Project`, `Package`, `Module`, `Symbol`, `Command`, `Dependency`, `ExecutionPlan`, `WorkspaceSnapshot` |
| `scanners/ignore.py` | `GitignoreParser`, `should_ignore_name`, `build_gitignore_matcher` |
| `scanners/walker.py` | `BoundedWalker` with `TraversalLimit` resource budgets; cancellation-aware; gitignore-aware |
| `detectors/fingerprint_detector.py` | Classifies repo as `EMPTY`, `DOC`, `LIB`, `CLI`, `BACKEND`, `FRONTEND`, `MONOREPO`, etc. |
| `detectors/project_detector.py` | Finds all projects via `pyproject.toml`, `package.json`, `Cargo.toml`, `docker-compose.yml` markers |
| `detectors/language_detector.py` | Extension-sampling language detection with confidence scores |
| `detectors/command_detector.py` | Infers commands from `package.json` scripts, `pyproject.toml` build/test, docker-compose |
| `parsers/python_parser.py` | stdlib `ast` parsing; regex fallback for unparseable files; extracts imports, exports, signatures |
| `parsers/js_parser.py` | Regex extraction for JS/MJS/CJS/TS/TSX; `import`/`require`/`export` parsing |
| `graph/symbol_index.py` | In-memory inverted index: `symbol_name → list[Symbol]`; exact + prefix search |
| `graph/workspace_graph.py` | Typed node/edge graph; add/query nodes and edges by id or path |
| `graph/dependency_graph.py` | Directed import graph; `topological_sort`, `find_cycles`, `affected_by` |
| `graph/command_registry.py` | In-memory command registry; lookup by name, prefix, kind, project |
| `planner/planner.py` | `WorkspacePlanner`: `plan_search`, `plan_read_file`, `plan_edit_file`, `plan_run_command` |
| `planner/risk_analyzer.py` | Risk level per step: `LOW(0)` → `MEDIUM(1)` → `HIGH(2)` → `CRITICAL(3)` |
| `planner/plan_validator.py` | Validates step count, duplicate IDs; rejects empty plans |
| `cache/cache_key.py` | SHA-256 content-addressed keys for workspace snapshots |
| `cache/workspace_cache.py` | Disk-backed JSON cache with LRU eviction (10k entries) |
| `cache/invalidator.py` | TTL-based + file-mtime invalidation |
| `events/` | WIK domain events: `WorkspaceScanned`, `PlanBuilt`, `PlanExecuted`, `CacheHit`, `CacheMiss`, `SymbolsIndexed` |
| `metrics/` | `WIKMetrics`: scan duration histogram, symbol count, cache hit rate, plan/step counts |
| `services/implementations.py` | `WorkspaceIntelligenceServiceImpl`: scan + plan + execute, all wired to event bus and metrics |

## Search Hierarchy

The WIK enforces a strict search hierarchy enforced at the middleware layer:

1. **SymbolIndex** (exact name / prefix match) — O(1) dict lookup
2. **WorkspaceGraph** (file nodes filtered by pattern)
3. **DependencyGraph** (who depends on X)
4. **BoundedWalker** — grep_files fallback, always bounded by `TraversalLimit`

`grep_files` is never removed. It is always the final fallback.

## Traversal Limits

| Limit | Value |
|---|---|
| `max_depth` | 6 |
| `max_files` | 50,000 |
| `max_dirs` | 10,000 |
| `max_duration_seconds` | 15.0 |
| `follow_symlinks` | false |

Limits are enforced strictly. The walker terminates as soon as any budget is exhausted.

## Execution Plans

`ExecutionStep.kind` values: `READ`, `WRITE`, `EDIT`, `DELETE`, `CREATE`, `RUN_COMMAND`, `RUN_TEST`, `RUN_BUILD`, `RUN_MIGRATION`, `START_SERVICE`, `STOP_SERVICE`, `RESTART_SERVICE`, `DEPLOY`, `ROLLBACK`, `CONFIRM`, `ASK_USER`

Risk levels: `LOW(0)`, `MEDIUM(1)`, `HIGH(2)`, `CRITICAL(3)`. `RiskAnalyzer` uses `.order` for severity comparisons (avoids `str, Enum` alphabetical ordering bug).

Plans are validated before execution: non-empty, ≤50 steps, no duplicate step IDs.

## Service Integration

`WorkspaceIntelligenceService` is registered in `ServiceContainer`:

```python
from deerflow.services.container import service_container

wis = service_container.workspace_intelligence_service()
snap = wis.scan("/path/to/repo")
result = wis.plan_search("def foo")
result = wis.plan_edit("/path/file.py", old="x = 1", new="x = 2")
result = wis.plan_run("test")
```

## Cache

Cache keys are SHA-256 of workspace metadata (root, kind, language, project count, file count). Cache directory: `{DEER_FLOW_HOME}/.deer-flow/workspace_cache/`. TTL: 3600s default.

## Phase C9 Coverage

| Phase | Status |
|---|---|
| 1-2: Models, scanners, detectors | ✅ Complete |
| 3: Parsers + graph construction | ✅ Complete |
| 4: Execution planning | ✅ Complete |
| 5: Caching | ✅ Complete |
| 6: Service layer integration | ✅ Complete |
| 7: Events + metrics | ✅ Complete |
| 8: Tests + docs | ✅ Complete |

## Testing

```bash
PYTHONPATH=../scripts uv run pytest tests/test_workspace_kernel.py -v
```

48 tests covering: models, detectors, parsers, graph algorithms, scanner bounds, planner, risk analysis, metrics, events, and service integration.
