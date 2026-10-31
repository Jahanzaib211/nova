"""Secrets: named credential files under the secrets dir and env-name
references. Values are never returned — presence, size and mtime only.

Two conventions already exist and this module unifies them: config refers
to secrets by *environment variable name* (``api_key_env``), and the ACP
overlay mounts 0600 files from ``~/.nova/secrets``. ``secrets.set`` writes
such a file; ``secrets.list`` reports which names exist on either side.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from deerflow.capabilities.modules._common import Empty, Items, Ok
from deerflow.capabilities.types import CapabilityModule, ModuleStatus, OpContext, Operation

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def secrets_dir() -> Path:
    return Path(os.environ.get("NOVA_SECRETS_DIR") or Path.home() / ".nova" / "secrets")


def _known_env_names() -> list[str]:
    """Env var names config.yaml points at (never their values)."""
    from deerflow.config.app_config import get_app_config

    names: set[str] = set()
    integ = getattr(get_app_config(), "integrations", None)
    for svc in (getattr(integ, "services", {}) or {}).values():
        if getattr(svc, "api_key_env", None):
            names.add(svc.api_key_env)
    for m in get_app_config().models:
        raw = (getattr(m, "extra", {}) or {}).get("api_key") or getattr(m, "api_key", None)
        if isinstance(raw, str) and raw.startswith("$"):
            names.add(raw[1:])
    return sorted(names)


def _file_entry(p: Path) -> dict[str, Any]:
    st = p.stat()
    return {
        "name": p.name,
        "source": "file",
        "present": st.st_size > 0,
        "bytes": st.st_size,
        "mode": oct(stat.S_IMODE(st.st_mode)),
        "secure": stat.S_IMODE(st.st_mode) == 0o600,
        "modified_at": int(st.st_mtime),
    }


async def _list(ctx: OpContext, inp: Empty) -> Items:
    items: list[dict[str, Any]] = []
    d = secrets_dir()
    if d.is_dir():
        items.extend(_file_entry(p) for p in sorted(d.iterdir()) if p.is_file())
    for name in _known_env_names():
        items.append({"name": name, "source": "env", "present": bool(os.environ.get(name, "").strip()), "bytes": None, "mode": None, "secure": True, "modified_at": None})
    return Items(items=items, total=len(items))


class SetIn(BaseModel):
    name: str = Field(description="File name under the secrets dir (letters, digits, _ . -)")
    value: str = Field(min_length=1, description="The secret. Stored 0600; never echoed back.")


class NameIn(BaseModel):
    name: str


def _path_for(name: str) -> Path:
    if not _NAME.match(name) or name in (".", ".."):
        raise ValueError("invalid secret name")
    return secrets_dir() / name


async def _set(ctx: OpContext, inp: SetIn) -> Ok:
    p = _path_for(inp.name)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(inp.value)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    return Ok(ok=True, detail=f"{inp.name} written ({len(inp.value)} bytes, 0600)")


async def _unset(ctx: OpContext, inp: NameIn) -> Ok:
    p = _path_for(inp.name)
    if not p.exists():
        return Ok(ok=False, detail="not present")
    p.unlink()
    return Ok(ok=True, detail=f"{inp.name} removed")


async def _status() -> ModuleStatus:
    d = secrets_dir()
    if not d.is_dir():
        return ModuleStatus(configured=False, healthy=True, detail=f"{d} does not exist yet")
    insecure = [p.name for p in d.iterdir() if p.is_file() and stat.S_IMODE(p.stat().st_mode) != 0o600]
    return ModuleStatus(configured=True, healthy=not insecure, detail="all 0600" if not insecure else f"not 0600: {', '.join(insecure)}")


MODULE = CapabilityModule(
    id="secrets",
    title="Secrets",
    description="Named secrets (0600 files under ~/.nova/secrets) and the env-var names config.yaml references. Values are never read back.",
    status=_status,
    operations=[
        Operation(name="secrets.list", kind="read", input=Empty, output=Items, handler=_list, description="Which secrets exist (presence only), with file mode and age.", harness=False, mcp=False, admin_only=True),
        Operation(name="secrets.set", kind="secret", input=SetIn, output=Ok, handler=_set, description="Write a secret file (0600, atomic).", harness=False, mcp=False, admin_only=True),
        Operation(name="secrets.unset", kind="secret", input=NameIn, output=Ok, handler=_unset, description="Remove a secret file.", harness=False, mcp=False, admin_only=True),
    ],
)
