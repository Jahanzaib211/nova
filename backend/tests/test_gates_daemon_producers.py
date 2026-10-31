"""Producer identity in scripts/gates/gates-daemon.py.

Each gate producer writes ``~/.nova/gates/<name>.json`` and the Nova Ops
console addresses it by the same id (``GATE_SPECS`` in
``nova-ops/src/lib/gates.ts``). A producer whose registered name does not
match its file is unaddressable: on 2026-09-21 the sandbox producer was
registered as ``sandbox_health`` while its file and card were
``sandbox-health``, so ``gates-daemon.py --only sandbox-health`` matched
nothing and exited having run no gate — silently, which is the part that
matters for a tool an operator reaches for during an incident.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "gates" / "gates-daemon.py"
spec = importlib.util.spec_from_file_location("gates_daemon", SCRIPT)
assert spec and spec.loader
daemon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daemon)


def _producers():
    return daemon.build_producers()


def test_no_gate_producer_name_uses_an_underscore():
    """Gate ids are hyphenated — they are filenames and console card ids.

    Only ``gate`` producers are constrained. A ``job`` producer's name is a key
    inside ``jobs.json`` rather than a filename, so ``prune_workspaces`` is
    correct as it stands and renaming it would break that file's continuity.
    """
    offenders = [p.name for p in _producers() if getattr(p, "kind", "gate") == "gate" and "_" in p.name]
    assert offenders == [], f"underscore in gate producer name(s): {offenders}"


def test_sandbox_health_is_addressable_by_its_file_name():
    names = {p.name for p in _producers()}
    assert "sandbox-health" in names
    assert "sandbox_health" not in names


def test_every_gate_producer_name_matches_its_script():
    """A ``gate`` producer's name should be recoverable from its command.

    Catches the rename-one-but-not-the-other drift directly.
    """
    for p in _producers():
        if getattr(p, "kind", "gate") != "gate":
            continue
        script = " ".join(p.argv)
        if p.name in ("ci", "inventory", "jobrunner", "lighthouse"):
            continue  # deliberately named for the subject, not the script
        assert p.name in script, f"producer {p.name!r} does not appear in its command {script!r}"


def test_producer_names_are_unique():
    names = [p.name for p in _producers()]
    assert len(names) == len(set(names)), f"duplicate producer names: {names}"


# ---------------------------------------------------------------------------
# Cross-repo: the console's cards and this daemon's producers are one system.
# ---------------------------------------------------------------------------

NOVA_OPS_GATES = Path.home() / "Desktop" / "nova-ops" / "src" / "lib" / "gates.ts"


def _console_card_ids() -> set[str]:
    """Card ids declared in nova-ops/src/lib/gates.ts."""
    import re

    text = NOVA_OPS_GATES.read_text()
    specs = text[text.index("export const GATE_SPECS") :]
    return set(re.findall(r'^\s*id:\s*"([^"]+)"', specs, re.MULTILINE))


@pytest.mark.skipif(not NOVA_OPS_GATES.exists(), reason="nova-ops console not checked out beside nova")
def test_every_gate_producer_has_a_console_card():
    """A gate nobody can see is a gate nobody reads.

    ``jobs.json`` was written for a year with no card to render it.
    """
    produced = {p.name for p in _producers() if getattr(p, "kind", "gate") == "gate"}
    missing = sorted(produced - _console_card_ids())
    assert missing == [], f"producers with no console card: {missing}"


@pytest.mark.skipif(not NOVA_OPS_GATES.exists(), reason="nova-ops console not checked out beside nova")
def test_the_daemon_stamps_its_cadence_for_the_console():
    """The console reads ``producer_interval_sec`` rather than re-declaring it.

    Two declarations drift: ci ran every 21,600 s against a console-declared
    86,400 s, and lighthouse every 21,600 s against 172,800 s.
    """
    assert "producer_interval_sec" in NOVA_OPS_GATES.read_text(), "console no longer reads the producer's cadence"
    assert "_stamp_interval" in SCRIPT.read_text(), "daemon no longer publishes its cadence"
