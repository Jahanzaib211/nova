# Phase C9 — Workspace Intelligence Kernel: Implementation Plan

## Purpose

This plan documents the full Phase C9 implementation. Read it before writing any code.

---

## 1. Architectural Decisions (settled)

### 1.1 Package location
`packages/harness/deerflow/workspace/` — parallel to `execution/`. Workspace intelligence is a kernel, same level as the execution kernel.

```
packages/harness/deerflow/
├── execution/     # Phase C7–C8: process supervision, PTY, cancellation
└── workspace/     # Phase C9: repository intelligence, planning, context
```

### 1.2 Service layer integration
A new `WorkspaceIntelligenceService` protocol added to `services/protocols.py`, implementation in `services/implementations.py`, wired into `ServiceContainer`. DI-first, same pattern as all other services.

### 1.3 Scope: one workspace per thread session
- Indexed lazily on first access (not eager on startup)
- Per-thread isolation via the existing `thread_id` dimension
- Cached in-memory for the process lifetime, with hash-invalidation for incremental refresh
- Indexing is cancellable (uses `ExecutionKernel` with bounded resources)

### 1.4 Language initial scope
**Python + JavaScript/TypeScript (Node.js)** first. Others (Rust, Go, Java) are Phase C10 candidates.

Rationale: these cover the Nova codebase itself and the most common user projects. Each language gets a dedicated detector + parser module.

### 1.5 Graph nodes are typed, edges are typed
```
Node types:   Project, Package, Module, File, Directory, Symbol, Command, Dependency
Edge types:   contains, imports, exports, runs, builds, deploys, requires, implements
```

### 1.6 Traversal is always bounded
- Max depth: configurable (default 20)
- Max files per walk: 50,000 hard cap
- Max nodes in graph: 100,000
- Symlinks: detected, not followed unless allowlisted
- `.gitignore` and `node_modules` excluded by default
- Timeouts enforced via Execution Kernel

### 1.7 Grep-first workflows are replaced, not augmented
The `grep_files` tool call is intercepted by `WorkspaceMiddleware` and redirected to the symbol index. Shell-based discovery is blocked at the middleware layer — not removed from the tool registry (for backward compat in non-workspace-aware contexts).

### 1.8 Symbol analysis: AST preferred, regex fallback
- Python: `ast` module + `sympy` or hand-rolled visitor for import/export analysis
- JavaScript/TypeScript: `typescript` parser + `ts-morph` for type-aware analysis
- Fallback: regex for files too large to parse (> 1MB)

### 1.9 Execution planning: structured plan, not LLM guessing
The WIK produces a typed `ExecutionPlan` dataclass before any shell call:
```
ExecutionPlan:
  steps: list[ExecutionStep]
  affected_files: set[str]
  affected_projects: set[str]
  risk_level: RiskLevel  (low/medium/high/critical)
  rollback_plan: list[str]  # how to undo each step
  reasoning: str  # human-readable why this plan was chosen
```

### 1.10 Confidence engine
Every detector returns `(value, confidence: float)` where confidence is 0.0–1.0. Plans are ranked by aggregate confidence. Detectors that are uncertain surface warnings, never errors.

---

## 2. Package Structure

```
packages/harness/deerflow/workspace/
├── __init__.py                    # exports public API
│
├── models/                        # Domain models (frozen dataclasses)
│   ├── __init__.py
│   ├── node.py                   # NodeType enum, Node dataclass, Edge dataclass
│   ├── project.py                # Project, Package, Module types
│   ├── symbol.py                 # Symbol, SymbolKind enum
│   ├── command.py                # Command, CommandKind enum
│   ├── dependency.py              # Dependency, DepKind enum
│   ├── execution_plan.py         # ExecutionStep, ExecutionPlan, RiskLevel
│   ├── workspace_snapshot.py      # WorkspaceSnapshot: full indexed state
│   └── fingerprint.py             # RepositoryFingerprint, RepoKind enum
│
├── scanners/                      # Bounded filesystem traversal
│   ├── __init__.py
│   ├── walker.py                 # BoundedWalker: os.walk with limits, gitignore
│   └── ignore.py                 # IGNORE_PATTERNS, should_ignore(), allowlist
│
├── detectors/                     # Language-agnostic + per-language detectors
│   ├── __init__.py
│   ├── fingerprint_detector.py    # RepoKind detection (empty/doc/lib/cli/etc.)
│   ├── project_detector.py       # find_projects(), find_packages()
│   ├── language_detector.py      # detect_languages() → (lang, confidence)
│   ├── package_manager_detector.py  # npm/pnpm/yarn/pip/uv/cargo
│   ├── capability_detector.py    # infer_capabilities() from project metadata
│   ├── entrypoint_detector.py    # find_entrypoints() (package.json scripts, pyproject.toml entry-points, Cargo binaries)
│   └── command_detector.py       # build_command_registry()
│
├── parsers/                      # Per-language AST/content parsers
│   ├── __init__.py
│   ├── base.py                   # LanguageParser abstract base
│   ├── python_parser.py          # Python AST visitor, import/export analysis
│   ├── js_parser.py              # JS/TS parser, import/export, JSX
│   └── typescript_parser.py       # TypeScript-specific: interfaces, types, generics
│
├── graph/                        # Graph construction and query
│   ├── __init__.py
│   ├── workspace_graph.py         # WorkspaceGraph: nodes + edges + adjacency
│   ├── dependency_graph.py        # DependencyGraphBuilder
│   ├── symbol_index.py            # SymbolIndex: symbol → locations
│   ├── command_registry.py        # CommandRegistry: name → Command
│   └── impact_analyzer.py         # ImpactAnalyzer: file → affected components
│
├── planner/                      # Execution planning
│   ├── __init__.py
│   ├── planner.py                # WorkspacePlanner: graph + goal → ExecutionPlan
│   ├── risk_analyzer.py          # RiskLevel, risk factors
│   └── plan_validator.py          # validate_plan() — checks reachability
│
├── cache/                        # Persistent + in-memory cache
│   ├── __init__.py
│   ├── workspace_cache.py         # WorkspaceCache: thread_id → WorkspaceSnapshot
│   ├── cache_key.py              # build_cache_key() — hash of file manifest
│   └── invalidator.py            # CacheInvalidator: mtime + hash invalidation
│
├── events/                       # Domain events for the event bus
│   ├── __init__.py
│   └── workspace_events.py         # WorkspaceScanned, ProjectDetected, GraphBuilt, PlanGenerated
│
├── metrics/                      # Observability
│   ├── __init__.py
│   └── workspace_metrics.py        # WorkspaceMetrics dataclass
│
├── tests/
│   ├── __init__.py
│   ├── test_walker.py
│   ├── test_fingerprint_detector.py
│   ├── test_language_detector.py
│   ├── test_project_detector.py
│   ├── test_python_parser.py
│   ├── test_js_parser.py
│   ├── test_workspace_graph.py
│   ├── test_dependency_graph.py
│   ├── test_symbol_index.py
│   ├── test_command_registry.py
│   ├── test_impact_analyzer.py
│   ├── test_planner.py
│   ├── test_cache.py
│   ├── test_invalidator.py
│   └── test_workspace_events.py
│
└── docs/
    └── WORKSPACE_KERNEL.md        # Full architecture doc (parallel to EXECUTION_KERNEL.md)
```

---

## 3. Implementation Phases

### Phase 1: Foundation + Models

**Goal**: Package scaffold + domain models. No I/O, no parsing.

1. Create `workspace/` package structure
2. `models/node.py`: `NodeType(NodeKind)`, `Node`, `Edge` frozen dataclasses
3. `models/project.py`: `Project`, `Package`, `Module` frozen dataclasses
4. `models/symbol.py`: `SymbolKind`, `Symbol` frozen dataclass
5. `models/command.py`: `CommandKind`, `Command` frozen dataclass
6. `models/dependency.py`: `DepKind`, `Dependency` frozen dataclass
7. `models/execution_plan.py`: `RiskLevel`, `ExecutionStep`, `ExecutionPlan`
8. `models/workspace_snapshot.py`: `WorkspaceSnapshot` — the full indexed state
9. `models/fingerprint.py`: `RepoKind`, `RepositoryFingerprint`
10. Write unit tests for all models (model invariants, serialization round-trips)
11. Write `WORKSPACE_KERNEL.md`

**Exit gate**: All model unit tests pass. Ruff clean.

---

### Phase 2: Scanners + Detection

**Goal**: Deterministic bounded filesystem traversal + project/language detection.

1. `scanners/ignore.py`: Port and extend `sandbox/search.py` ignore patterns. Add `GitignoreParser` to read actual `.gitignore` files.
2. `scanners/walker.py`: `BoundedWalker` class — wraps `os.walk`, enforces depth/file/cpu limits, produces `(root, dirs, files)` with filtered dirs in-place. Supports symlink detection.
3. `detectors/fingerprint_detector.py`: `FingerprintDetector` — inspects top-level files to classify repo kind (empty, doc, library, cli, backend, frontend, monorepo, polyrepo). Returns `(RepositoryFingerprint, confidence)`.
4. `detectors/project_detector.py`: `ProjectDetector` — finds all projects (package.json, pyproject.toml, Cargo.toml, go.mod, etc.) within workspace. Returns `list[Project]`.
5. `detectors/language_detector.py`: `LanguageDetector` — aggregates language signals from projects. Returns `list[(Language, confidence)]`.
6. `detectors/package_manager_detector.py`: `PackageManagerDetector` — detects package managers from lockfiles (package-lock.json, pnpm-lock.yaml, poetry.lock, Pipfile.lock, Cargo.lock).
7. `detectors/capability_detector.py`: `CapabilityDetector` — infers capabilities from project metadata (e.g., `scripts.dev` → `has_dev_server`, `scripts.build` → `has_build`).
8. `detectors/entrance_detector.py`: `EntranceDetector` — finds entry points for each project type.
9. `detectors/command_detector.py`: `CommandDetector` — builds the `CommandRegistry` from all detected projects' scripts/entry-points.

Write integration tests using real fixture repos (Python project, Node project, monorepo).

**Exit gate**: All scanner + detector tests pass. BoundedWalker correctly terminates on deep trees. Ruff clean.

---

### Phase 3: Parsers + Graph Construction

**Goal**: Parse code files, build the workspace graph and symbol index.

1. `parsers/base.py`: `LanguageParser` abstract base — `parse_file(path)`, `extract_imports(path)`, `extract_exports(path)`
2. `parsers/python_parser.py`: `PythonParser` using `ast` module — parses `.py` files, extracts imports (`import X`, `from X import y`), exports (`__all__`, top-level definitions with kind via `sympy` or visitor pattern). Handles `SyntaxError` gracefully (skip unparseable files).
3. `parsers/js_parser.py`: `JSParser` using `esprima` or `acorn` — parses `.js`/`.mjs`/`.cjs` files, extracts imports/exports.
4. `parsers/typescript_parser.py`: `TSTSParser` using `ts-morph` or `typescript` — parses `.ts`/`.tsx`, type-aware imports/exports, interface definitions.
5. `graph/workspace_graph.py`: `WorkspaceGraph` — builds the full `Node` + `Edge` graph from scanner results + parser results. Thread-safe for concurrent access.
6. `graph/dependency_graph.py`: `DependencyGraphBuilder` — builds runtime + build dependency graph from import edges. Nodes: packages/modules. Edges: `requires`.
7. `graph/symbol_index.py`: `SymbolIndex` — in-memory inverted index: `symbol_name → list[SymbolLocation]`. Supports prefix search. Backed by `dict` + `sortedcontainers` for range queries.
8. `graph/command_registry.py`: `CommandRegistry` — `dict[command_name, Command]` from Phase 2. Includes project context and inferred args.
9. `graph/impact_analyzer.py`: `ImpactAnalyzer` — given a set of modified files, returns what projects, packages, commands, and symbols are affected. Uses reverse dependency graph.

Write integration tests: build graph from the Nova codebase itself, verify expected nodes exist.

**Exit gate**: Graph of Nova codebase is complete (verified manually). Symbol index returns correct results for known symbols. Ruff clean.

---

### Phase 4: Execution Planning

**Goal**: Given a user goal, generate a structured execution plan using the workspace graph.

1. `planner/risk_analyzer.py`: `RiskAnalyzer` — given a plan's steps and affected files, compute `RiskLevel`. Factors: file count, destructive operations (delete, overwrite), deployment-affecting files, migration files.
2. `planner/plan_validator.py`: `PlanValidator` — given an `ExecutionPlan`, verify: all referenced files exist, all referenced commands are in registry, plan steps are in valid order.
3. `planner/planner.py`: `WorkspacePlanner` — the main class. Takes `WorkspaceSnapshot` + `user_goal(str)`. Uses the LLM (via existing model factory) to reason over the graph and produce an `ExecutionPlan`. The LLM call is given: workspace summary, relevant projects, relevant symbols, command registry. NOT the full codebase. The LLM returns a structured JSON that is parsed into `ExecutionPlan`.

**Critical constraint**: The LLM prompt for planning must include the full workspace graph summary (not raw files). The WIK generates the summary from the graph — never passes raw file listings to the planner LLM.

**Planner LLM prompt structure**:
```
You are the Nova Workspace Planner. Given the following workspace:

=== WORKSPACE SUMMARY ===
{workspace_summary}
=== DETECTED PROJECTS ===
{projects}
=== DETECTED COMMANDS ===
{commands}
=== RELEVANT SYMBOLS ===
{symbols}
=== USER GOAL ===
{user_goal}

Produce an execution plan...
```

**Exit gate**: Planner produces valid `ExecutionPlan` for at least 5 test goals (create file, edit function, add test, run build, add dependency). Plans pass `PlanValidator`. Ruff clean.

---

### Phase 5: Caching + Persistence

**Goal**: Make workspace intelligence reusable across agent turns without re-indexing.

1. `cache/cache_key.py`: `build_cache_key(thread_id, workspace_root)` — hash of file manifest (path + mtime + size) for all files under workspace. Fast: O(n) single pass, early-exit if manifest unchanged.
2. `cache/workspace_cache.py`: `WorkspaceCache` — `dict[thread_id, tuple[cache_key, WorkspaceSnapshot]]`. In-memory LRU with configurable max size. Eviction policy: LRU by last access time.
3. `cache/invalidator.py`: `CacheInvalidator` — given a `cache_key` and a list of changed files, determines whether cache is valid, partially valid (incremental update), or must be fully rebuilt. Uses git diff when available for speed.
4. Integrate caching into `WorkspaceKernel.index()` — check cache before scanning. Return cached snapshot if valid. Trigger background re-index if stale (but serve stale while re-indexing).

**Cache lifecycle**:
```
index(thread_id)
  → build_cache_key()
  → check WorkspaceCache
      → cache hit + valid → return cached WorkspaceSnapshot
      → cache hit + stale → background_reindex(), return cached (serve stale)
      → cache miss → full_index() → store in cache → return snapshot
```

**Exit gate**: Second `index()` call on same thread_id returns instantly (cache hit). Cache invalidation correctly detects changed files. Ruff clean.

---

### Phase 6: Service Layer + Middleware Integration

**Goal**: Wire WIK into the Nova service layer and intercept agent tool calls.

1. Add `WorkspaceIntelligenceService` protocol to `services/protocols.py`:
   ```python
   class WorkspaceIntelligenceService(Protocol):
       async def index(thread_id, force_refresh) -> WorkspaceSnapshot
       async def get_snapshot(thread_id) -> WorkspaceSnapshot | None
       async def plan(thread_id, goal) -> ExecutionPlan
       def get_command(thread_id, name) -> Command | None
       def find_symbol(thread_id, name) -> list[Symbol]
       def analyze_impact(thread_id, files) -> ImpactReport
       def get_metrics(thread_id) -> WorkspaceMetrics
   ```
2. Add `WorkspaceIntelligenceImpl` in `services/implementations.py`
3. Register in `ServiceContainer`
4. Create `WorkspaceMiddleware` in `agents/middlewares/workspace_middleware.py`:
   - Intercepts `grep_files` calls → routes to `SymbolIndex.find()`
   - Intercepts `search_files` calls → routes to `WorkspaceGraph.glob()`
   - Blocks direct shell traversal of unknown directories (ls, find without graph context)
   - On first tool call in a thread: triggers background `index()`
5. Publish events: `WorkspaceScanned`, `ProjectDetected`, `GraphBuilt`, `PlanGenerated` to the event bus
6. Wire into gateway lifespan: call `workspace_kernel.shutdown()` on gateway exit

**Exit gate**: `grep_files` tool returns symbol-index results when WIK is active. Cache hit ratio metric is non-zero after 2 indexing passes. Ruff clean.

---

### Phase 7: Metrics + Observability

**Goal**: Full observability of the WIK.

1. `metrics/workspace_metrics.py`: `WorkspaceMetrics` frozen dataclass:
   ```
   load_time_ms, graph_generation_time_ms, repository_size_bytes,
   projects_detected, languages_detected, capabilities_detected,
   commands_detected, cache_hit_ratio, traversal_count,
   planner_latency_ms, ast_parse_latency_ms, context_compression_ratio
   ```
2. Wire into `DiagnosticsServiceImpl` — all WIK metrics are recorded as diagnostics
3. Emit `WorkspaceScanned` event with full metrics payload after each index
4. Add WIK metrics to the existing `/api/diagnostics` endpoint

**Exit gate**: All metrics are non-zero after first index. Metrics endpoint returns WIK data. Ruff clean.

---

### Phase 8: Testing + Documentation

**Goal**: ≥80% coverage for all new code. Docs synchronized.

1. Write remaining tests: Phase 3–7 unit tests, integration tests
2. Test fixtures:
   - Python project (pyproject.toml + src/ + tests/)
   - Node project (package.json + src/ + tests/)
   - Monorepo (pnpm workspace with 2 packages)
   - Mixed repo (Python backend + Node frontend)
   - Empty workspace
   - Documentation-only repo
3. Stress test: 100k-file simulation (generated), verify bounded walker terminates in < 60s
4. Cancellation test: index cancelled mid-walk, no orphan threads
5. Sync docs: `WORKSPACE_KERNEL.md`, `ARCHITECTURE.md`, `NOVA_CHANGELOG.md`, `CONSOLIDATION.md`

**Exit gate**: `backend/tests/test_workspace_*.py` ≥ 80% coverage. All docs link-valid. Ruff clean.

---

## 4. Open Questions (for user clarification)

### Q1: Planner LLM — dedicated model or shared model?
Should the `WorkspacePlanner` use a dedicated fast/cheap model (e.g., a local Llama variant) or reuse the session's model? Using a separate model avoids consuming the session's context window but adds latency. Recommendation: share the session model for now, make it configurable.

### Q2: WIK activation — opt-in or opt-out?
Should workspace intelligence be active by default for all threads, or only when the user has explicitly enabled it? Recommendation: opt-in via `config.yaml` (`workspace.intelligence_enabled: true`) with a migration path to default-on after sufficient production validation.

### Q3: What exactly does "grep-first workflows are eliminated" mean operationally?
The `grep_files` tool should be replaced by the symbol index. But should the tool still exist as a fallback? Recommendation: keep `grep_files` as a fallback that falls back to the bounded `search.py` walker when symbol index has no match — never raw shell.

### Q4: Should WIK index the entire `/mnt/user-data/workspace` or scoped subdirectories?
The workspace can contain multiple independent projects. Recommendation: WIK indexes the full workspace (monorepo-friendly) but groups results by project. The graph includes cross-project dependency edges when detected.

### Q5: Monorepo support depth?
The spec mentions monorepo detection. How deep should project-in-project nesting be detected? Recommendation: detect up to 3 levels of nesting (root → package → sub-package), no deeper.

---

## 5. Dependency Inventory

### Python stdlib only (no new external deps)
- `ast` — Python parsing
- `os`, `re`, `hashlib`, `json`, `pathlib` — traversal, hashing
- `concurrent.futures` — parallel scanning
- `threading` / `asyncio` — background indexing

### Third-party (must already be in project or added intentionally)
- `typescript` or `ts-morph` — TypeScript parsing (verify existing dep or add)
- `esprima` or `acorn` — JavaScript parsing (verify existing dep)
- `sortedcontainers` — symbol index range queries (add if not present)

**Before Phase 3**: verify/add these deps in `pyproject.toml`.

---

## 6. Execution Kernel Integration Points

The WIK uses the `ExecutionKernel` for bounded file I/O:

```python
# Scanning is a supervised execution
req = ExecutionRequest(
    argv=("find", workspace_root, "-type", "f", "-name", "*.py"),
    execution_class=ExecutionClass.SHELL,
    limits=ResourceLimits(timeout=30.0, max_output_bytes=10_000_000),
)
result = kernel.execute_sync(req)
```

This ensures:
- Traversal always terminates (timeout enforced)
- Output is bounded (max_output_bytes enforced)
- Cancellation is propagated through the heartbeat thread
- Audit trail is complete

---

## 7. Rollback Plan

If Phase C9 introduces regressions:
```bash
git revert <C9-start-sha> -- packages/harness/deerflow/workspace/
git revert <C9-start-sha> -- services/protocols.py services/implementations.py
git revert <C9-start-sha> -- agents/middlewares/workspace_middleware.py  # if created
```

All Phase C8 code is untouched. WIK is additive only.

---

## 8. Success Criteria

- [ ] `workspace/` package exists with all modules from §2
- [ ] BoundedWalker terminates on 100k-file repo in < 60s
- [ ] Workspace graph of Nova backend has ≥ 500 nodes
- [ ] Symbol index returns results for known Nova symbols
- [ ] Command registry has ≥ 20 commands for Nova workspace
- [ ] Execution plans are generated for test goals and pass validation
- [ ] Cache hit ratio > 0 after 2 consecutive index calls
- [ ] All WIK tests pass (≥ 80% coverage)
- [ ] Backend suite: 0 new failures (5570 + new tests pass)
- [ ] Frontend suite: 0 regressions
- [ ] Cross-ref check: clean
- [ ] Ruff: clean
- [ ] Docs: WORKSPACE_KERNEL.md + NOVA_CHANGELOG.md updated
