"""Every file the Helm chart reads must be committed to git.

Flux only ever sees committed files. `.Files.Get` on a path that isn't in the
clone returns an **empty string** — no error, no warning — so the chart renders
a ConfigMap with empty data and both `helm upgrade` and the HelmRelease report
success. The pods then fail on whatever the missing content was supposed to
provide.

This is not hypothetical. The root .gitignore carries an unanchored
`config.yaml` pattern (correct for the operator-local file, which holds real
credentials), and it also matched `k8s/charts/nova/files/config.yaml` — a chart
*source* whose every secret is a `$ENV_VAR` reference. Result:
`nova-app-config` was applied with `config.yaml: ""`, the gateway died in
`AppConfig.model_validate` on the missing required `sandbox` field, and
nova-staging sat in CrashLoopBackOff for five days (≈780 restarts) with nginx
crash-looping behind it because its liveness probe hit the dead gateway.

So: parse the chart templates for `.Files.Get "..."` and assert git tracks each
referenced path. A pure-git check — no cluster, no helm binary.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART_DIR = REPO_ROOT / "k8s" / "charts" / "nova"
TEMPLATES = CHART_DIR / "templates"

_FILES_GET = re.compile(r'\.Files\.Get\s+"([^"]+)"')


def _referenced_files() -> set[str]:
    refs: set[str] = set()
    for tpl in TEMPLATES.rglob("*.yaml"):
        refs |= set(_FILES_GET.findall(tpl.read_text(encoding="utf-8")))
    return refs


def _git_tracked(path: Path) -> bool:
    rel = path.relative_to(REPO_ROOT).as_posix()
    out = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return out.returncode == 0


pytestmark = pytest.mark.skipif(not (REPO_ROOT / ".git").exists(), reason="not a git checkout")


def test_chart_templates_reference_at_least_one_file() -> None:
    """Guard the guard — a broken regex would make every other test vacuous."""
    refs = _referenced_files()
    assert refs, "no .Files.Get references found; the parser is broken"
    assert "files/nginx.conf" in refs


@pytest.mark.parametrize("ref", sorted(_referenced_files()))
def test_referenced_chart_file_exists_on_disk(ref: str) -> None:
    assert (CHART_DIR / ref).is_file(), f"chart references {ref} but it does not exist"


@pytest.mark.parametrize("ref", sorted(_referenced_files()))
def test_referenced_chart_file_is_tracked_by_git(ref: str) -> None:
    """The actual regression: present locally, invisible to Flux."""
    path = CHART_DIR / ref
    assert _git_tracked(path), f"{path.relative_to(REPO_ROOT)} is referenced by a chart template but is NOT tracked by git — Flux will render it as an empty string and the deployment will fail silently. Check .gitignore."


def test_operator_local_config_stays_ignored() -> None:
    """The .gitignore negation must not have un-ignored the real secrets file."""
    out = subprocess.run(
        ["git", "check-ignore", "-q", "config.yaml"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    assert out.returncode == 0, "the repo-root config.yaml holds real credentials and must stay gitignored"


class TestHelmReleaseReconcileStrategy:
    """A git-sourced chart must repackage on git revision, not chart version.

    Flux's default `reconcileStrategy: ChartVersion` only rebuilds the chart
    artifact when Chart.yaml's `version:` changes. Ours is pinned at 0.1.0 and
    never bumped, so five days of commits touching chart templates and
    files/ produced no upgrade at all — while `flux get helmrelease` reported
    "Helm upgrade succeeded" the whole time and `helm history` sat at 2
    revisions. Every chart-content fix silently did nothing.
    """

    HELMRELEASE = REPO_ROOT / "k8s" / "flux" / "staging" / "helmrelease.yaml"

    def test_uses_revision_strategy(self) -> None:
        import yaml

        docs = [d for d in yaml.safe_load_all(self.HELMRELEASE.read_text(encoding="utf-8")) if d]
        hr = next(d for d in docs if d.get("kind") == "HelmRelease")
        chart_spec = hr["spec"]["chart"]["spec"]
        assert chart_spec.get("reconcileStrategy") == "Revision", "a git-sourced chart with a pinned version must use reconcileStrategy: Revision, or no chart-content change ever reaches the cluster"

    def test_chart_is_sourced_from_git(self) -> None:
        """The strategy above only matters for a GitRepository source."""
        import yaml

        docs = [d for d in yaml.safe_load_all(self.HELMRELEASE.read_text(encoding="utf-8")) if d]
        hr = next(d for d in docs if d.get("kind") == "HelmRelease")
        assert hr["spec"]["chart"]["spec"]["sourceRef"]["kind"] == "GitRepository"


class TestChartLabelSanitization:
    """`helm.sh/chart` must survive a SemVer build-metadata suffix.

    With `reconcileStrategy: Revision`, Flux packages the chart as
    `nova-0.1.0+<git-sha>`. `+` is not a legal Kubernetes label character, so
    without the standard `replace "+" "_"` every object in the chart fails
    server-side apply with "metadata.labels: Invalid value", the upgrade rolls
    back, and the release wedges. Helm's own `helm create` scaffold includes
    this replace; this chart was hand-written and omitted it, which turned the
    reconcileStrategy fix into a cluster-wide apply failure.
    """

    HELPERS = REPO_ROOT / "k8s" / "charts" / "nova" / "templates" / "_helpers.tpl"

    def test_chart_label_replaces_plus(self) -> None:
        text = self.HELPERS.read_text(encoding="utf-8")
        line = next(ln for ln in text.splitlines() if "helm.sh/chart:" in ln)
        assert 'replace "+" "_"' in line, f"helm.sh/chart must sanitize SemVer build metadata: {line.strip()}"

    def test_chart_label_is_truncated_to_the_label_limit(self) -> None:
        line = next(ln for ln in self.HELPERS.read_text(encoding="utf-8").splitlines() if "helm.sh/chart:" in ln)
        assert "trunc 63" in line, "label values are capped at 63 characters"
