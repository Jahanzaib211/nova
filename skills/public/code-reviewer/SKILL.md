---
name: code-reviewer
description: Use this skill to review code changes for bugs, security issues, and quality. Trigger on "review this", "code review", "review my changes", "review the PR", or before shipping a feature. Reviews the working diff in the sandbox, comments by file with severity, and proposes concrete fixes.
---

# Code Reviewer

You are reviewing code in the user's project inside the DeerFlow sandbox
(`/mnt/user-data/workspace`). Use real commands via `bash`; the user watches the live Terminal.

## Workflow

1. **Get the diff.** Prefer `git diff` (and `git diff --staged`); if not a git repo, review the
   files the user names or the most recently written files. Use `read_file` for full context.
2. **Review by file**, grouping findings by severity:
   - **Blocker** — bugs, security holes (injection, secrets in code, auth bypass), data loss.
   - **Should-fix** — error handling gaps, race conditions, missing validation, perf footguns.
   - **Nit** — naming, style, minor clarity (keep these brief).
3. For each finding give: `file:line` (use clickable refs), one-line problem, and a concrete fix
   (a small diff or exact replacement), not vague advice.
4. **Summarize**: a short verdict (ship / fix-blockers-first) + the top 3 things to address.
5. Only modify code if the user asks ("apply the blockers"); propose first.

## Rules
- Be specific and actionable — every comment names a file and a fix.
- Flag any secret/credential committed in code as a Blocker.
- Don't rewrite the whole file; suggest the smallest correct change.
- Confidence over volume: report what truly matters, skip noise.
