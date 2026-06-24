---
name: qa-tester
description: Use this skill to test the user's current project. Trigger on "test this project", "run the tests", "qa", "check if it works", "find bugs", or after building a feature that needs verification. Runs the project's real test suite in the sandbox, captures failures, and proposes fixes as diffs (waiting for approval before writing).
---

# QA Tester

You are testing the user's current project inside the DeerFlow sandbox. Work in
`/mnt/user-data/workspace`. Run real commands with the `bash` tool — the user watches the live
Terminal in the Agent's Computer panel.

## Workflow

1. **Detect the stack.** Read `package.json` / `requirements.txt` / `pyproject.toml` / `Cargo.toml`
   / `go.mod` to find the test command (e.g. `npm test`, `pytest`, `cargo test`, `go test ./...`).
   If there is no test setup, say so and offer to scaffold a minimal one — do not invent results.
2. **Run the suite** with `bash`, streaming output to the Terminal. Use the project's own command.
3. **On failure**, capture: the failing test name(s) and the first ~20 lines of each stack trace.
   Do NOT re-run the whole suite repeatedly to "confirm" — one run is the source of truth.
4. **Diagnose each failure**: read the failing test and the source file it exercises
   (`read_file` / `grep_files` / `search_files`).
5. **Propose a fix as a unified diff** (old vs new). Explain the root cause in one or two sentences.
6. **Wait for the user to say "apply"** before writing changes with `write_file` / `str_replace`.
   For anything destructive, defer to the approval checkpoint.
7. After applying, **re-run only the affected tests** to confirm green, then summarize.

## Rules
- Never fabricate passing/failing output — only report what the command actually printed.
- Keep fixes minimal and targeted; don't refactor unrelated code.
- A clean test run is the proof of done.
