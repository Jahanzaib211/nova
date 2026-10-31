# docs/audit

Self-probe outputs and audit artifacts.

## Contents

| File | Date | Description |
|---|---|---|
| `2026-08-14-self-probe.md` | 2026-08-14 | Full-stack self-probe (write_file integrity, sandbox health, UI regression) |
| `2026-08-29-self-probe.md` | 2026-08-29 | Full-stack self-audit — backend 6798 passed, frontend 635 passed, write-file truncation CLOSED; Playwright 77/85 green after symlinking chromium-1234 to revision 1217 |

## Running a self-probe

See `.opencode/skill/nova-self-audit/SKILL.md` for the workflow, or:

```bash
make self-audit
```
