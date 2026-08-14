---
name: nova-self-audit
description: Run a full-stack self-audit probe, collect outputs, and compare against the previous run. Use when the user asks "run a self-audit", "check Nova's health", or "verify everything still works". Produces a timestamped report in docs/audit/.
---

# nova-self-audit

## Quick start

```bash
make self-audit
```

## What it does

1. **Backend hermetic gate**: `cd backend && PYTHONPATH=. uv run pytest tests/ -x -q --tb=line`
2. **Frontend hermetic gate**: `cd frontend && pnpm check && pnpm test`
3. **Playwright mocked E2E**: `cd frontend && pnpm test:e2e`
4. **Blocking-IO gate**: `cd backend && make test-blocking-io`
5. **Docker sandbox status**: `scripts/docker.sh status`
6. **Write-file integrity**: `cd backend && PYTHONPATH=. uv run pytest tests/test_write_file_no_truncation.py -v`

## Outputs

| Output | Location |
|---|---|
| Test summary | stdout |
| Timestamped report | `docs/audit/YYYY-MM-DD-self-probe.md` |
| Raw probe zip | `.nova/self-audit/` (gitignored) |

## Comparing against previous run

After running, diff the new report against the previous one:

```bash
diff docs/audit/2026-08-14-self-probe.md docs/audit/$(date +%Y-%m-%d)-self-probe.md
```

## Cleanup

```bash
make self-audit-clean
```
