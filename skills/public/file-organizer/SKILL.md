---
name: file-organizer
description: Use this skill to organize a folder. Trigger on "organize this folder", "clean up these files", "sort my files", "propose a folder structure". Scans a directory in the sandbox, groups files by topic/type, and proposes a new structure — moving files only after approval.
---

# File Organizer

You organize a folder inside the DeerFlow sandbox (default `/mnt/user-data/workspace`, or the path
the user names). Use `bash` / `ls` / `search_files`; the user watches the live Terminal.

## Workflow

1. **Scan** the target folder (`ls`, `search_files`) and read enough of ambiguous files to classify
   them (`read_file` first lines). Note counts and obvious groups (by type, project, topic, date).
2. **Propose a structure** as a tree (e.g. `docs/`, `src/`, `assets/`, `archive/`) with which files
   move where and why. Call out duplicates and junk (e.g. `.DS_Store`, build artifacts).
3. **Wait for approval** before moving anything. Moves/deletes are destructive — defer to the
   approval checkpoint; never `rm` without explicit confirmation.
4. On approval, perform the moves with `bash` (`mkdir -p`, `mv`), preserving content; report the
   final tree with `ls`.

## Rules
- Never delete data without explicit approval; prefer moving to `archive/` over deleting.
- Keep the structure shallow and obvious; don't over-nest.
- Show the before/after tree so the change is auditable.
