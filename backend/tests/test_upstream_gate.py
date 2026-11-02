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


def test_pre_rebase_hook_blocks_rebase_onto_upstream(repos):
    """git hands pre-rebase only <upstream>, never the --onto target, and no
    rebase state exists yet -- so `--onto upstream/main <base>` used to slip
    past. The hook now reads the --onto target from the parent git command."""
    nova = repos["nova"]
    gate.install_hooks(nova)
    env = {**os.environ, **GIT_ENV}
    out = subprocess.run(["git", "-C", str(nova), "rebase", "--onto", "upstream/main", "HEAD~1"], capture_output=True, text=True, env=env)
    assert out.returncode != 0
    assert "foreign root commit" in out.stdout + out.stderr
    assert "pre-rebase hook refused" in out.stderr
    assert git(nova, "log", "-1", "--format=%s") == "nova: own changes"


@pytest.mark.parametrize(
    ("argv", "onto"),
    [
        (["git", "rebase", "--onto", "upstream/main", "base"], "upstream/main"),
        (["git", "rebase", "--onto=upstream/main", "base"], "upstream/main"),
        (["git", "-C", "/x", "rebase", "-q", "--onto", "u/m", "b", "topic"], "u/m"),
        (["git", "rebase", "base"], None),
        (["git", "rebase", "--onto"], None),
        (["git", "commit", "--onto", "x"], None),
        ([], None),
    ],
)
def test_onto_is_parsed_from_the_rebase_command_line(argv, onto):
    assert gate.parse_rebase_onto(argv) == onto


def test_pre_rebase_inspects_the_onto_target_as_well_as_the_upstream(repos, monkeypatch):
    nova = repos["nova"]
    base = git(nova, "rev-parse", "HEAD~1")
    monkeypatch.setattr(gate, "_parent_cmdline", lambda: ["git", "rebase", "--onto", "upstream/main", base])
    report = gate._hook_report(nova, "pre-rebase", [base])
    assert statuses(report)["history_roots"] == gate.RED


def test_pre_rebase_without_a_readable_parent_still_checks_the_upstream(repos, monkeypatch):
    nova = repos["nova"]
    monkeypatch.setattr(gate, "_parent_cmdline", lambda: [])
    report = gate._hook_report(nova, "pre-rebase", ["HEAD~1"])
    assert statuses(report)["history_roots"] == gate.GREEN
    # Says where the real enforcement is, since --onto may be invisible here.
    assert "pre-push" in next(c for c in report["checks"] if c["name"] == "history_roots")["detail"]


# --- hook resolution -----------------------------------------------------
#
# Hooks live in the common git dir, so one installed hook serves every
# worktree and every branch -- including branches that predate the gate.


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env={**os.environ, **GIT_ENV})


def test_hook_prefers_the_gate_in_the_current_worktree(repos, tmp_path):
    nova = repos["nova"]
    gate.install_hooks(nova)
    marker = tmp_path / "worktree-gate-ran"
    stub = nova / "scripts" / "gates" / "upstream-gate.py"
    stub.parent.mkdir(parents=True)
    stub.write_text(f"import pathlib\npathlib.Path({str(marker)!r}).write_text('yes')\n")
    out = _run(nova, "commit", "-q", "--allow-empty", "-m", "uses the worktree's own gate")
    assert out.returncode == 0, out.stderr
    assert marker.read_text() == "yes"


def test_hook_falls_back_to_the_installed_gate(repos):
    # The fixture repo has no scripts/gates/ of its own -- like a branch that
    # predates the gate -- so the installed copy must still guard it.
    nova = repos["nova"]
    gate.install_hooks(nova)
    out = _run(nova, "merge", "--allow-unrelated-histories", "-X", "ours", "-m", "merge upstream", "upstream/main")
    assert out.returncode != 0
    assert "foreign root commit" in out.stdout + out.stderr


def test_missing_gate_fails_closed_for_pre_push(repos, tmp_path):
    nova = repos["nova"]
    gate.install_hooks(nova, script=tmp_path / "gone" / "upstream-gate.py")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(nova, "remote", "add", "origin", str(remote))
    out = _run(nova, "push", "-q", "origin", "main")
    assert out.returncode != 0
    assert "cannot find the upstream gate" in out.stderr
    assert git(remote, "branch", "--list") == ""


def test_missing_gate_warns_but_allows_local_commits(repos, tmp_path):
    nova = repos["nova"]
    gate.install_hooks(nova, script=tmp_path / "gone" / "upstream-gate.py")
    out = _run(nova, "commit", "-q", "--allow-empty", "-m", "still allowed")
    assert out.returncode == 0, out.stderr
    assert "cannot find the upstream gate" in out.stderr
    assert git(nova, "log", "-1", "--format=%s") == "still allowed"


# --- provenance ----------------------------------------------------------


def port_commit(repo: Path, files: dict[str, str], subject: str, ref_value: str) -> str:
    return commit(repo, files, f"{subject}\n\nUpstream-Ref: {ref_value}")


def test_provenance_green_for_a_real_port(repos):
    nova = repos["nova"]
    port_commit(nova, {"a.py": "a = 2\n"}, "fix(a): bump a", f"bytedance/deer-flow@{repos['clean']}")
    report = gate.verify_provenance(nova)
    assert report["status"] == gate.GREEN, report
    assert report["ported"] == 1


def test_provenance_without_trailers_is_green(repos):
    report = gate.verify_provenance(repos["nova"])
    assert report["status"] == gate.GREEN
    assert report["ported"] == 0


def test_provenance_rejects_a_malformed_value(repos):
    nova = repos["nova"]
    port_commit(nova, {"a.py": "a = 2\n"}, "fix(a): bump a", f"deer-flow@{repos['clean'][:12]}")
    assert statuses(gate.verify_provenance(nova))["provenance_format"] == gate.RED


def test_provenance_rejects_a_sha_that_is_not_upstream(repos):
    nova = repos["nova"]
    own = git(nova, "rev-parse", "HEAD")
    port_commit(nova, {"a.py": "a = 2\n"}, "fix(a): bump a", f"bytedance/deer-flow@{own}")
    assert statuses(gate.verify_provenance(nova))["provenance_upstream"] == gate.RED


def test_provenance_rejects_the_fork_base_itself(repos):
    nova = repos["nova"]
    port_commit(nova, {"a.py": "a = 2\n"}, "fix(a): bump a", f"bytedance/deer-flow@{gate.FORK_BASE}")
    assert statuses(gate.verify_provenance(nova))["provenance_upstream"] == gate.RED


def test_provenance_rejects_a_duplicate_claim(repos):
    nova = repos["nova"]
    value = f"bytedance/deer-flow@{repos['clean']}"
    port_commit(nova, {"a.py": "a = 2\n"}, "fix(a): bump a", value)
    port_commit(nova, {"a.py": "a = 3\n"}, "fix(a): bump a again", value)
    report = gate.verify_provenance(nova)
    assert statuses(report)["provenance_unique"] == gate.RED
    assert repos["clean"][:12] in next(c for c in report["checks"] if c["name"] == "provenance_unique")["detail"]


def test_provenance_flags_a_duplicate_of_a_claim_outside_the_range(repos):
    # A PR re-porting a commit main already carries must fail, even though the
    # first claim is not in the PR's range.
    nova = repos["nova"]
    value = f"bytedance/deer-flow@{repos['clean']}"
    port_commit(nova, {"a.py": "a = 2\n"}, "fix(a): bump a", value)
    base = git(nova, "rev-parse", "HEAD")
    port_commit(nova, {"a.py": "a = 3\n"}, "fix(a): bump a again", value)
    assert statuses(gate.verify_provenance(nova, rev_range=f"{base}..HEAD"))["provenance_unique"] == gate.RED


def test_provenance_ignores_old_history_outside_the_range(repos):
    nova = repos["nova"]
    port_commit(nova, {"a.py": "a = 2\n"}, "fix(a): sloppy old port", "not-a-ref")
    base = git(nova, "rev-parse", "HEAD")
    port_commit(nova, {"b.py": "b = 9\n"}, "fix(b): good port", f"bytedance/deer-flow@{repos['conflict']}")
    report = gate.verify_provenance(nova, rev_range=f"{base}..HEAD")
    assert report["status"] == gate.GREEN, report
    assert report["ported"] == 1


def test_provenance_is_yellow_without_the_upstream_ref(repos):
    nova = repos["nova"]
    port_commit(nova, {"a.py": "a = 2\n"}, "fix(a): bump a", f"bytedance/deer-flow@{repos['clean']}")
    git(nova, "remote", "remove", "upstream")
    report = gate.verify_provenance(nova)
    check = next(c for c in report["checks"] if c["name"] == "provenance_upstream")
    assert check["status"] == gate.YELLOW
    assert "git fetch" in check["detail"]
    assert report["status"] == gate.YELLOW


def test_provenance_default_range_is_relative_to_origin_main(repos, tmp_path):
    nova = repos["nova"]
    port_commit(nova, {"a.py": "a = 2\n"}, "fix(a): sloppy port already on main", "not-a-ref")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(nova, "remote", "add", "origin", str(remote))
    git(nova, "push", "-q", "origin", "main")
    git(nova, "fetch", "-q", "origin")
    report = gate.verify_provenance(nova)
    assert report["range"] == "origin/main..HEAD"
    assert report["status"] == gate.GREEN
    assert gate.verify_provenance(nova, rev_range="HEAD")["status"] == gate.RED
