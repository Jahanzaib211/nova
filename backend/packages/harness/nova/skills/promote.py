"""Promote a skill built/installed in a thread workspace to the GLOBAL registry.

This is the "global plugin skill system": a skill the agent creates in its
workspace (``/mnt/user-data/workspace/<name>/SKILL.md`` + optional supporting
files) is copied — security-scanned — into ``skills/custom/<name>`` on the host,
which is mounted into every sandbox and survives container teardown. Custom skills
default to *enabled* (``ExtensionsConfig.is_skill_enabled``), so a promoted skill
immediately appears in the ✨ launcher and is activatable via ``/<name>`` in any
future thread.

Reuses existing machinery: ``SkillStorage`` (write/validate), ``scan_skill_content``
(security), and ``refresh_skills_system_prompt_cache_async`` (registry refresh).
"""

from __future__ import annotations

import logging
from pathlib import Path

from deerflow.skills.security_scanner import scan_skill_content
from deerflow.skills.storage import get_or_new_skill_storage
from deerflow.skills.storage.skill_storage import SkillStorage
from deerflow.skills.types import SKILL_MD_FILE

logger = logging.getLogger(__name__)

_SKIP = {".git", "node_modules", "__pycache__", ".venv", ".cache"}
_MAX_FILE_BYTES = 256_000
_MAX_FILES = 40


async def promote_skill_to_global(name: str, source_dir: Path, *, thread_id: str | None = None) -> dict:
    """Copy a skill directory into the global custom-skill registry (scanned).

    Args:
        name: Skill name (hyphen-case); becomes ``skills/custom/<name>``.
        source_dir: Host path to the skill directory containing SKILL.md.
        thread_id: Originating thread (for history/audit only).

    Returns: ``{"saved": bool, "name": str, "files": [...], "reason": str}``.
    """
    name = SkillStorage.validate_skill_name(name)
    source_dir = Path(source_dir)
    skill_md = source_dir / SKILL_MD_FILE
    if not skill_md.is_file():
        return {"saved": False, "name": name, "files": [], "reason": f"no SKILL.md in {source_dir}"}

    storage = get_or_new_skill_storage()

    # SKILL.md — validate frontmatter + security scan (non-executable).
    md_content = skill_md.read_text(encoding="utf-8", errors="replace")
    storage.validate_skill_markdown_content(name, md_content)
    md_scan = await scan_skill_content(md_content, executable=False, location=f"{name}/{SKILL_MD_FILE}")
    if md_scan.decision == "block":
        return {"saved": False, "name": name, "files": [], "reason": f"security scan blocked SKILL.md: {md_scan.reason}"}

    written: list[str] = []
    storage.write_custom_skill(name, SKILL_MD_FILE, md_content)
    written.append(SKILL_MD_FILE)

    # Supporting files (scripts/, references/, …) — scan executables strictly.
    count = 0
    for path in sorted(source_dir.rglob("*")):
        if path.is_dir() or path == skill_md:
            continue
        rel = path.relative_to(source_dir)
        if any(seg in _SKIP or seg.startswith(".") for seg in rel.parts):
            continue
        if path.stat().st_size > _MAX_FILE_BYTES:
            continue
        count += 1
        if count > _MAX_FILES:
            break
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # skip binaries
        executable = rel.parts and rel.parts[0] == "scripts"
        scan = await scan_skill_content(content, executable=bool(executable), location=f"{name}/{rel.as_posix()}")
        if scan.decision == "block" or (executable and scan.decision != "allow"):
            logger.warning("save_skill: skipped %s (%s)", rel, scan.reason)
            continue
        storage.write_custom_skill(name, rel.as_posix(), content)
        written.append(rel.as_posix())

    # Refresh the in-process skills/system-prompt cache so it's available now.
    try:
        from deerflow.agents.lead_agent.prompt import refresh_skills_system_prompt_cache_async

        await refresh_skills_system_prompt_cache_async()
    except Exception:
        logger.debug("save_skill: prompt cache refresh failed", exc_info=True)

    return {"saved": True, "name": name, "files": written, "reason": "ok"}
