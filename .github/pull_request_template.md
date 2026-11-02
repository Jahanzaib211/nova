<!-- Reference a related issue with #123. Use Fixes / Closes / Resolves to
     auto-close it on merge. Delete this line if the PR doesn't reference an issue. -->
Fixes #

## Why

<!-- Why are you opening this PR? Cover two things:
       - The trigger — what made you write this? A bug you hit, a feature you need,
         tech debt, or a prod issue?
       - The pain being addressed — user-facing problem, or what it unblocks.
     For non-trivial features, please open an issue/discussion first to align on
     scope before writing code. -->


## What changed

<!-- Describe the change from a user's / caller's perspective, not as a code diff. e.g.:
       - "Settings now has a 'Custom endpoint' field, off by default"
       - "Backend /api/chat gains a `stream` flag, defaults to false"
       - "Default model changed from X to Y — existing users notice on first run" -->


## Surface area

<!-- Check every box that applies. Reviewers use this to scope the review. -->

- [ ] **Frontend UI** — page / component / setting / interaction under `frontend/`
- [ ] **Backend API** — endpoint / SSE event / request-response shape under `backend/app`
- [ ] **Agents / LangGraph** — agent node, graph wiring, `langgraph.json`, or prompt change
- [ ] **Sandbox** — `docker/` or sandboxed execution
- [ ] **Skills** — change under `skills/`
- [ ] **Dependencies** — new/upgraded entry in `backend/pyproject.toml` or `frontend/package.json` (say what it buys us)
- [ ] **Default behavior change** — changes existing behavior without the user opting in (default model, default setting, data shape)
- [ ] **Docs / tests / CI only** — no runtime behavior change


## Screenshots / Recording

<!-- If you checked "Frontend UI", attach screenshots showing the entry point —
     where users discover the change — not just the feature in isolation.
     Before/after is best for behavior changes. Short GIFs welcome. -->


## Bug fix verification

<!-- Skip (delete) this section if this PR is not a bug fix.

     Bugs should be encoded as a failing test that goes red before the fix.
     Confirm:
       - Test path that reproduces the bug:
       - Did it go red on `main` and green on this branch? (yes / no)
       - If a red test wasn't cheap to write, explain why and what you did instead. -->


## Validation

<!-- What you actually ran. Run at least the checks for the area you changed:
       Backend:   cd backend  && make lint && make test
       Frontend:  cd frontend && pnpm format && pnpm lint && pnpm typecheck && BETTER_AUTH_SECRET=local-dev-secret pnpm build && make test
       Frontend E2E (if you touched frontend/): cd frontend && make test-e2e -->


## Upstream provenance

<!-- Delete this section if nothing here came from bytedance/deer-flow.
     Ported code arrives only as patches via `scripts/gates/upstream-gate.py port`,
     never as a merge, pull or rebase; each ported commit carries the trailers the
     tool writes, which the "Upstream isolation" check verifies. -->

- Upstream commit(s): `bytedance/deer-flow@<sha>` (upstream PR #…)
- What Nova changed while porting, and why:


## Self-test (AGENTS.md)

- [ ] `cd backend && PYTHONPATH=../scripts uv run pytest tests/test_healthcheck_daemon.py tests/test_no_cross_references.py tests/test_upstream_gate.py -v`
- [ ] `cd frontend && pnpm check && pnpm test`
- [ ] `python3 scripts/gates/upstream-gate.py check` is GREEN
- [ ] `git status` is clean


## AI assistance

<!-- Nova is an AI project — most PRs here use AI coding tools, and that's
     welcome. Disclosing it just helps reviewers calibrate how closely to read the
     diff. Please fill all three; don't delete the section. -->

**Tool(s) used:** <!-- e.g. Claude Code, Cursor, GitHub Copilot, Codex, Windsurf, or "none" -->

**How you used it:** <!-- e.g. "generated the module from a spec", "autocomplete only",
     "AI wrote tests, I wrote the impl". A prompt or conversation link is great too. -->

- [ ] I've read and understand every line of this change and take responsibility for it — it's not unreviewed AI output.

