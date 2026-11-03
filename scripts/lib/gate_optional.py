"""Optional stacks: services a deployment has declared it does not run.

Why this exists
---------------
The inventory and capabilities gates probe every integration config.yaml
names (Mailcow, Chatwoot, Twenty, OpenClaw, ...). On a host that never
deployed one of them, the probe is correct -- nothing answers -- and the gate
is red for good. A gate that is red every cycle is a gate nobody reads, which
is how real outages go unnoticed beside it.

``NOVA_GATE_OPTIONAL_STACKS`` (comma or space separated, case-insensitive)
names the stacks this deployment treats as optional::

    NOVA_GATE_OPTIONAL_STACKS=mailcow,chatwoot,twenty,openclaw

It is read from the environment first, then from the repo-root ``.env`` (the
gate producers run under PM2 without that file loaded, the same reason
capabilities-gate.py reads ``NOVA_OPS_TOKEN`` from it).

The rule that keeps this from hiding a real failure
---------------------------------------------------
A declared stack is reported ``disabled`` (green) only when **none** of its
items is healthy -- the stack is simply not deployed. If any item of the
stack answers, the stack *is* running, so whatever else of it is down is a
real failure and keeps its red/yellow status. Stacks that are not declared
are never touched.

An item belongs to stack ``s`` when its id is ``s`` or starts with ``s_``
(``mailcow_smtp``, ``mailcow_imap``, ``mailcow_api`` -> ``mailcow``;
``openclaw_cli`` -> ``openclaw``). No per-service table to keep in sync.

Stdlib only: imported by gate scripts that run under the system interpreter.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

ENV_KEY = "NOVA_GATE_OPTIONAL_STACKS"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Item health values (contracts/integrations_health_contract.json) that mean
#: the item is up. "disabled" is not "up": a stack that is all-disabled is
#: already green and needs no rewrite.
_UP = {"healthy"}


def parse_stacks(raw: str | None) -> frozenset[str]:
    """``"Mailcow, chatwoot twenty"`` -> ``{"mailcow", "chatwoot", "twenty"}``."""
    if not raw:
        return frozenset()
    return frozenset(part.strip().lower() for part in re.split(r"[,\s]+", raw.strip().strip("\"'")) if part.strip())


def _from_env_file(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    value = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{ENV_KEY}="):
            value = stripped.split("=", 1)[1].split(" #", 1)[0].strip()
    return value


def optional_stacks(environ: Mapping[str, str] | None = None, env_file: Path | None = None) -> frozenset[str]:
    """The declared optional stacks: environment first, then ``.env``.

    An explicitly empty environment value wins over the file, so a test or an
    operator can switch the feature off for one run.
    """
    env = os.environ if environ is None else environ
    if ENV_KEY in env:
        return parse_stacks(env.get(ENV_KEY))
    return parse_stacks(_from_env_file(env_file or REPO_ROOT / ".env"))


def stack_of(item_id: str, stacks: Iterable[str]) -> str | None:
    """The declared stack ``item_id`` belongs to, if any (longest match wins)."""
    ident = item_id.lower()
    best = None
    for stack in stacks:
        if ident == stack or ident.startswith(stack + "_"):
            if best is None or len(stack) > len(best):
                best = stack
    return best


def apply_to_inventory(checks: list[dict], stacks: Iterable[str]) -> list[dict]:
    """Rewrite the items of not-deployed optional stacks to ``disabled``.

    ``checks`` are inventory items (``id``, ``health``, ``status``,
    ``detail``). Returns a new list; the input is not mutated. A stack with at
    least one healthy item is left exactly as probed.
    """
    stacks = frozenset(stacks)
    if not stacks:
        return list(checks)
    members: dict[str, list[int]] = {}
    for index, entry in enumerate(checks):
        stack = stack_of(str(entry.get("id") or entry.get("name") or ""), stacks)
        if stack:
            members.setdefault(stack, []).append(index)
    out = list(checks)
    for stack, indexes in members.items():
        if any(checks[i].get("health") in _UP for i in indexes):
            continue  # the stack is running: anything of it that is down is real
        for i in indexes:
            entry = checks[i]
            if entry.get("health") == "disabled":
                continue
            out[i] = {
                **entry,
                "status": "green",
                "health": "disabled",
                "detail": f"optional stack '{stack}' not deployed ({ENV_KEY}); probe said: {entry.get('detail', '')}"[:240],
                "optional_stack": stack,
                "probed_health": entry.get("health"),
            }
    return out


_FAILING_RE = re.compile(r"(\d+) required failing: ([^;()]+)")


def failing_outside(detail: str, stacks: Iterable[str]) -> tuple[list[str], list[str]] | None:
    """Split a capability module's "N required failing: a, b, c" detail.

    Returns ``(optional, other)`` -- the failing names inside and outside the
    declared stacks -- or None when the detail does not carry a complete list
    (no match, or the gateway truncated it so the count does not add up; then
    the caller must not assume anything is optional).
    """
    match = _FAILING_RE.search(detail or "")
    if not match:
        return None
    names = [n.strip() for n in match.group(2).split(",") if n.strip()]
    if len(names) != int(match.group(1)):
        return None
    stacks = frozenset(stacks)
    optional = [n for n in names if stack_of(n, stacks)]
    other = [n for n in names if not stack_of(n, stacks)]
    return optional, other
