"""Unit tests for scripts/gates/upstream-gate.py (upstream isolation gate + miner).

Each test builds throwaway repositories that mirror the real shape: a "nova"
history with its own root whose first tree equals the upstream fork base, and an
"upstream" history with a different root that kept moving after the fork.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gates" / "upstream-gate.py"
spec = importlib.util.spec_from_file_location("upstream_gate", SCRIPT)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(repo: Path, *args: str, stdin: str | None = None) -> str:
    env = {**os.environ, **GIT_ENV}
    out = subprocess.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True, text=True, env=env, check=True)
    return out.stdout.strip()


def commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, body in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


BASE_FILES = {"a.py": "a = 1\n", "b.py": "b = 1\n", "c.py": "c = 1\n"}


@pytest.fixture
def repos(tmp_path, monkeypatch):
    up = tmp_path / "upstream"
    up.mkdir()
    git(up, "init", "-q", "-b", "main")
    commit(up, {"seed.txt": "upstream root\n"}, "upstream root")
    (up / "seed.txt").unlink()
    base = commit(up, BASE_FILES, "Prepare release (#3603)")

    clean_fix = commit(up, {"a.py": "a = 2\n"}, "fix(a): bump a (#7001)")
    conflict_fix = commit(up, {"b.py": "b = 'upstream'\n"}, "fix(b): upstream b")
    present_fix = commit(up, {"c.py": "c = 2\n"}, "fix(security): harden c against injection")
    feature = commit(up, {"new.py": "new = True\n"}, "feat(new): add new module")

    nova = tmp_path / "nova"
    nova.mkdir()
    git(nova, "init", "-q", "-b", "main")
    nova_root = commit(nova, BASE_FILES, "fork: start nova")
    commit(nova, {"b.py": "b = 'nova'\n", "c.py": "c = 2\n"}, "nova: own changes")
    git(nova, "remote", "add", "upstream", str(up))
    git(nova, "remote", "set-url", "--push", "upstream", "DISABLED")
    git(nova, "fetch", "-q", "upstream")

    # The env override (not a monkeypatch) so hook subprocesses see it too.
    monkeypatch.setenv("NOVA_UPSTREAM_GATE_ROOTS", nova_root)
    monkeypatch.setattr(gate, "FORK_BASE", base)
    monkeypatch.setattr(gate, "UPSTREAM_URL_RE", gate.re.compile(r"/upstream/?$"))
    monkeypatch.setattr(gate, "LEDGER_PATH", "docs/upstream-ledger.json")
    return {
        "nova": nova,
        "up": up,
        "clean": clean_fix,
        "conflict": conflict_fix,
        "present": present_fix,
        "feature": feature,
    }


def statuses(report: dict) -> dict[str, str]:
    return {c["name"]: c["status"] for c in report["checks"]}


# --- check ---------------------------------------------------------------


def test_own_history_is_green(repos):
    report = gate.build_check_report(repos["nova"])
    assert report["status"] == gate.GREEN, report
    assert statuses(report)["history_roots"] == gate.GREEN


def test_merging_upstream_history_is_red(repos):
    nova = repos["nova"]
    git(nova, "merge", "-q", "--allow-unrelated-histories", "-X", "ours", "-m", "merge upstream", "upstream/main")
    report = gate.build_check_report(nova)
    assert statuses(report)["history_roots"] == gate.RED
    assert report["status"] == gate.RED


def test_rebasing_onto_upstream_is_red(repos):
    nova = repos["nova"]
    git(nova, "switch", "-q", "-c", "rebased", "upstream/main")
    commit(nova, {"z.py": "z = 1\n"}, "nova work on top of upstream")
    assert statuses(gate.build_check_report(nova))["history_roots"] == gate.RED


def test_shallow_clone_cannot_prove_isolation(repos, tmp_path):
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{repos['nova']}", str(shallow)], check=True, env={**os.environ, **GIT_ENV})
    report = gate.build_check_report(shallow)
    assert statuses(report)["history_roots"] == gate.RED
    assert "fetch-depth" in next(c for c in report["checks"] if c["name"] == "history_roots")["detail"]


def test_pushable_upstream_remote_is_red(repos):
    nova = repos["nova"]
    git(nova, "config", "--unset", "remote.upstream.pushurl")
    assert statuses(gate.build_check_report(nova))["upstream_push_disabled"] == gate.RED


def test_no_upstream_remote_is_green(repos):
    nova = repos["nova"]
    git(nova, "remote", "remove", "upstream")
    report = gate.build_check_report(nova)
    assert statuses(report)["upstream_push_disabled"] == gate.GREEN
    assert report["status"] == gate.GREEN


def test_gh_defaulting_to_upstream_is_red(repos):
    nova = repos["nova"]
    git(nova, "config", "remote.upstream.gh-resolved", "base")
    assert statuses(gate.build_check_report(nova))["gh_default_repo"] == gate.RED


def test_pre_push_inspects_the_pushed_commits(repos):
    nova = repos["nova"]
    upstream_tip = git(nova, "rev-parse", "upstream/main")
    own_tip = git(nova, "rev-parse", "HEAD")
    zero = "0" * 40
    bad = f"refs/heads/x {upstream_tip} refs/heads/x {zero}\n"
    good = f"refs/heads/main {own_tip} refs/heads/main {zero}\n"
    deletion = f"(delete) {zero} refs/heads/old {own_tip}\n"
    assert statuses(gate.build_check_report(nova, pushed=gate.parse_pre_push(bad)))["history_roots"] == gate.RED
    assert statuses(gate.build_check_report(nova, pushed=gate.parse_pre_push(good + deletion)))["history_roots"] == gate.GREEN


def test_pushing_to_the_upstream_url_is_red(repos):
    nova = repos["nova"]
    report = gate.build_check_report(nova, push_url=str(repos["up"]))
    assert statuses(report)["push_target"] == gate.RED
    assert statuses(gate.build_check_report(nova, push_url="https://github.com/Jahanzaib211/nova.git"))["push_target"] == gate.GREEN


def test_pending_merge_head_is_inspected(repos):
    nova = repos["nova"]
    (nova / ".git" / "MERGE_HEAD").write_text(git(nova, "rev-parse", "upstream/main") + "\n")
    assert statuses(gate.build_check_report(nova, include_merge_head=True))["history_roots"] == gate.RED


# --- mine ----------------------------------------------------------------


def by_sha(rows):
    return {r["sha"]: r for r in rows}


def test_mine_classifies_applicability(repos):
    rows = by_sha(gate.mine(repos["nova"], ref="upstream/main"))
    assert rows[repos["clean"]]["apply"] == "clean"
    assert rows[repos["conflict"]]["apply"] == "conflict"
    assert rows[repos["present"]]["apply"] == "present"
    assert rows[repos["feature"]]["apply"] == "clean"
    assert rows[repos["present"]]["security"] is True
    assert rows[repos["clean"]]["security"] is False
    assert rows[repos["feature"]]["type"] == "feat"
    assert rows[repos["conflict"]]["nova_modified"] == ["b.py"]


def test_mine_hides_ported_and_skipped_commits(repos):
    nova = repos["nova"]
    commit(nova, {"a.py": "a = 2\n"}, f"fix(a): bump a\n\nUpstream-Ref: bytedance/deer-flow@{repos['clean']}")
    gate.record_skip(nova, repos["feature"], "not wanted in nova")
    shas = set(by_sha(gate.mine(nova, ref="upstream/main")))
    assert repos["clean"] not in shas
    assert repos["feature"] not in shas
    assert repos["conflict"] in shas
    ledger = json.loads((nova / "docs" / "upstream-ledger.json").read_text())
    assert ledger["skipped"][repos["feature"]]["reason"] == "not wanted in nova"


def test_mine_without_upstream_ref_explains_how_to_fetch(repos):
    nova = repos["nova"]
    git(nova, "remote", "remove", "upstream")
    with pytest.raises(gate.GateError, match="git fetch upstream"):
        gate.mine(nova, ref="upstream/main")


# --- port ----------------------------------------------------------------


def test_port_stages_the_patch_and_writes_provenance(repos):
    nova = repos["nova"]
    result = gate.port(nova, repos["clean"], ref="upstream/main")
    assert (nova / "a.py").read_text() == "a = 2\n"
    assert git(nova, "diff", "--cached", "--name-only") == "a.py"
    message = Path(result["message_file"]).read_text()
    # Upstream PR numbers would link to unrelated Nova PRs, so they move to a trailer.
    assert message.splitlines()[0] == "fix(a): bump a"
    assert f"Upstream-Ref: bytedance/deer-flow@{repos['clean']}" in message
    assert "Upstream-PR: bytedance/deer-flow#7001" in message
    # Nothing is committed for the operator: tests run first.
    assert git(nova, "log", "-1", "--format=%s") == "nova: own changes"
    # Porting never adds upstream history.
    git(nova, "commit", "-q", "-F", result["message_file"])
    assert statuses(gate.build_check_report(nova))["history_roots"] == gate.GREEN


def test_port_refuses_commits_that_are_not_upstream(repos):
    nova = repos["nova"]
    with pytest.raises(gate.GateError, match="not an upstream commit"):
        gate.port(nova, git(nova, "rev-parse", "HEAD"), ref="upstream/main")


def test_port_refuses_an_already_ported_commit(repos):
    nova = repos["nova"]
    commit(nova, {"a.py": "a = 2\n"}, f"fix(a): bump a\n\nUpstream-Ref: bytedance/deer-flow@{repos['clean']}")
    with pytest.raises(gate.GateError, match="already ported"):
        gate.port(nova, repos["clean"], ref="upstream/main")


def test_port_refuses_a_dirty_tree(repos):
    nova = repos["nova"]
    (nova / "a.py").write_text("a = 'wip'\n")
    with pytest.raises(gate.GateError, match="uncommitted"):
        gate.port(nova, repos["clean"], ref="upstream/main")


def test_port_leaves_conflicts_for_the_operator(repos):
    nova = repos["nova"]
    result = gate.port(nova, repos["conflict"], ref="upstream/main")
    assert result["conflicts"] == ["b.py"]
    assert "<<<<<<<" in (nova / "b.py").read_text()


# --- hooks ---------------------------------------------------------------


def test_install_hooks_writes_marked_hooks_and_keeps_foreign_ones(repos):
    nova = repos["nova"]
    hooks = nova / ".git" / "hooks"
    (hooks / "pre-rebase").write_text("#!/bin/sh\necho mine\n")
    result = gate.install_hooks(nova)
    assert result["installed"] == ["commit-msg", "pre-merge-commit", "pre-push"]
    for name in ("pre-push", "pre-merge-commit"):
        body = (hooks / name).read_text()
        assert gate.HOOK_MARKER in body
        assert os.access(hooks / name, os.X_OK)
    assert result["skipped"] == ["pre-rebase"]
    assert (hooks / "pre-rebase").read_text() == "#!/bin/sh\necho mine\n"
    # Re-running is idempotent for hooks it owns.
    assert gate.install_hooks(nova)["installed"] == ["commit-msg", "pre-merge-commit", "pre-push"]


def test_commit_msg_hook_blocks_a_conflicted_merge_being_concluded(repos):
    # A merge that stops on conflicts never runs pre-merge-commit: the final
    # `git commit` is an ordinary commit, so commit-msg has to catch it.
    nova = repos["nova"]
    gate.install_hooks(nova)
    env = {**os.environ, **GIT_ENV}
    merge = subprocess.run(["git", "-C", str(nova), "merge", "--allow-unrelated-histories", "upstream/main"], capture_output=True, text=True, env=env)
    assert merge.returncode != 0
    git(nova, "add", "-A")
    out = subprocess.run(["git", "-C", str(nova), "commit", "--no-edit"], capture_output=True, text=True, env=env)
    assert out.returncode != 0
    assert "foreign root commit" in out.stdout + out.stderr
    assert git(nova, "log", "-1", "--format=%s") == "nova: own changes"


def test_pre_rebase_hook_blocks_rebasing_onto_upstream(repos):
    nova = repos["nova"]
    gate.install_hooks(nova)
    env = {**os.environ, **GIT_ENV}
    out = subprocess.run(["git", "-C", str(nova), "rebase", "upstream/main"], capture_output=True, text=True, env=env)
    assert out.returncode != 0
    # The gate refused it -- not a rebase that merely failed on conflicts.
    assert "foreign root commit" in out.stdout + out.stderr
    assert "pre-rebase hook refused" in out.stderr
    assert git(nova, "log", "-1", "--format=%s") == "nova: own changes"
