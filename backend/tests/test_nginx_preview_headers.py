"""Guard the nginx layer that renders the Agent's Computer previews.

This exists because of a deploy-only regression that no test could see: the
Browser tab rendered agent-built apps fine on localhost and blank on every
deployment. Cause was entirely in nginx. `docker/nginx/nginx.conf` sets

    add_header X-Frame-Options       "DENY" always;
    add_header Content-Security-Policy $csp_policy always;

at *server* level, and `$csp_policy` maps `~^/api/` to `default-src 'none'`.
Since no location block overrode them, both landed on the preview proxy
responses (`/api/sandbox/preview/...`), which the UI loads in an <iframe>:
`X-Frame-Options: DENY` blocked the frame outright, and the browser intersects
multiple CSP headers, so the gateway's own `sandbox allow-scripts ...` was
ANDed with `default-src 'none'` — no scripts, styles or images either.

`docker/nginx/nginx.local.conf` sets no headers at all and `make dev` bypasses
nginx entirely, so the whole class of bug was invisible locally. Nothing in CI
starts nginx (the Playwright suites run against `pnpm start` on :3000), so the
config files were, until this module, covered by exactly zero automated checks.

These are pure-text assertions over the config files — no nginx required, so
they run in the normal unit-test job.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every nginx config that terminates browser traffic for a Nova deployment.
CONFIGS = {
    "docker": REPO_ROOT / "docker" / "nginx" / "nginx.conf",
    "docker-tls": REPO_ROOT / "docker" / "nginx" / "nginx.tls.conf",
    "local": REPO_ROOT / "docker" / "nginx" / "nginx.local.conf",
    "k8s": REPO_ROOT / "k8s" / "charts" / "nova" / "files" / "nginx.conf",
}

# The locations that serve iframe-embedded content.
SANDBOX_LOCATION = "location ~ ^/api/sandbox/(preview|lpreview|absproxy|appview)(-ws)?/"
ARTIFACTS_LOCATION = "location ~ ^/api/threads/[^/]+/artifacts"

# The security headers that must NOT be re-emitted inside those locations.
# `add_header` in a location replaces the inherited server-level set, so simply
# not naming them is what disables them — naming them again re-breaks framing.
FRAMING_HOSTILE = ("X-Frame-Options", "Content-Security-Policy")


def _read(name: str) -> str:
    path = CONFIGS[name]
    assert path.is_file(), f"missing nginx config: {path}"
    return path.read_text(encoding="utf-8")


def _location_body(text: str, header: str) -> str:
    """Return the brace-balanced body of the location block starting at ``header``."""
    start = text.index(header)
    open_brace = text.index("{", start)
    depth = 0
    for i in range(open_brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1 : i]
    raise AssertionError(f"unbalanced braces after {header!r}")


def _strip_comments(body: str) -> str:
    return "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))


ALL = sorted(CONFIGS)
# nginx.local.conf sets no security headers anywhere, so it has no global set to
# override; it still carries the blocks for WebSocket support and for structural
# parity with the deployed configs (that asymmetry is what hid the original bug).
HEADERED = ["docker", "docker-tls", "k8s"]


class TestSandboxPreviewLocation:
    @pytest.mark.parametrize("name", ALL)
    def test_location_exists(self, name: str) -> None:
        assert SANDBOX_LOCATION in _read(name), f"{name}: sandbox preview location missing — iframed previews will inherit the global security headers"

    @pytest.mark.parametrize("name", HEADERED)
    def test_does_not_reemit_framing_hostile_headers(self, name: str) -> None:
        body = _strip_comments(_location_body(_read(name), SANDBOX_LOCATION))
        for header in FRAMING_HOSTILE:
            assert f"add_header {header}" not in body.replace("  ", " "), f"{name}: re-emitting {header} inside the preview location re-blocks the iframe"

    @pytest.mark.parametrize("name", ALL)
    def test_supports_websocket_upgrade(self, name: str) -> None:
        """HMR, ttyd and noVNC are all WebSocket-driven."""
        body = _strip_comments(_location_body(_read(name), SANDBOX_LOCATION))
        assert "proxy_set_header Upgrade $http_upgrade;" in body, f"{name}: preview HMR/ttyd/noVNC sockets cannot upgrade"
        assert "proxy_set_header Connection $connection_upgrade;" in body, f"{name}: Connection header must come from the $connection_upgrade map"

    @pytest.mark.parametrize("name", ALL)
    def test_connection_upgrade_map_is_defined(self, name: str) -> None:
        text = _read(name)
        if "$connection_upgrade" not in text:
            pytest.skip(f"{name} does not reference the map")
        if name == "docker-tls":
            # Appended into docker/nginx.conf's http block by the entrypoint, so
            # it shares that file's map rather than declaring its own.
            assert "map $http_upgrade $connection_upgrade" in _read("docker")
            return
        assert re.search(r"map\s+\$http_upgrade\s+\$connection_upgrade\s*\{", text), f"{name}: uses $connection_upgrade without defining the map"

    @pytest.mark.parametrize("name", ALL)
    def test_proxies_to_the_gateway(self, name: str) -> None:
        body = _strip_comments(_location_body(_read(name), SANDBOX_LOCATION))
        assert "proxy_pass http://" in body, f"{name}: preview location must proxy to the gateway"


class TestArtifactsLocation:
    @pytest.mark.parametrize("name", ALL)
    def test_location_exists(self, name: str) -> None:
        assert ARTIFACTS_LOCATION in _read(name), f"{name}: artifact previews are iframed and need their own location"

    @pytest.mark.parametrize("name", HEADERED)
    def test_does_not_reemit_framing_hostile_headers(self, name: str) -> None:
        body = _strip_comments(_location_body(_read(name), ARTIFACTS_LOCATION))
        for header in FRAMING_HOSTILE:
            assert f"add_header {header}" not in body.replace("  ", " "), f"{name}: re-emitting {header} re-blocks artifact previews"

    @pytest.mark.parametrize("name", ALL)
    def test_ordered_before_the_general_threads_location(self, name: str) -> None:
        """nginx matches regex locations in source order.

        `location ~ ^/api/threads` also matches `/api/threads/<id>/artifacts`,
        so if it came first the artifact block would be dead config.
        """
        text = _read(name)
        general = "location ~ ^/api/threads {"
        if general not in text:
            pytest.skip(f"{name} has no general /api/threads regex location")
        assert text.index(ARTIFACTS_LOCATION) < text.index(general), f"{name}: artifacts location must precede the general /api/threads location or it never matches"


class TestGlobalPostureUnchanged:
    """The fix must not weaken the app shell's own headers."""

    @pytest.mark.parametrize("name", HEADERED)
    def test_server_level_headers_still_present(self, name: str) -> None:
        text = _read(name)
        assert 'add_header X-Frame-Options                "DENY" always;' in text
        assert "add_header Content-Security-Policy       $csp_policy always;" in text

    @pytest.mark.parametrize("name", ["docker", "k8s"])
    def test_app_shell_csp_allows_same_origin_frames(self, name: str) -> None:
        """The previews are same-origin, so `frame-src 'self'` is what permits them."""
        text = _read(name)
        assert "frame-src 'self' blob:;" in text, f"{name}: app shell must allow framing same-origin previews and blob: artifact previews"


class TestDockerAndK8sStayInSync:
    """k8s/charts/nova/files/nginx.conf is a hand-maintained copy of the docker one.

    The header block is documented as "ported verbatim". Nothing enforced that,
    and a fix applied to one and not the other is exactly how a deployment-only
    regression comes back on the other deployment.
    """

    def test_security_header_block_matches(self) -> None:
        def headers(text: str) -> list[str]:
            return sorted(re.sub(r"\s+", " ", m.group(0)).strip() for m in re.finditer(r"add_header\s+\S+\s+.*?always;", text))

        docker = set(headers(_read("docker")))
        k8s = set(headers(_read("k8s")))
        # Both must carry the same global set; the k8s file legitimately omits
        # nothing today, so require exact equality and let drift fail loudly.
        assert docker == k8s, f"docker/k8s nginx security headers drifted:\n  only in docker: {sorted(docker - k8s)}\n  only in k8s:    {sorted(k8s - docker)}"

    def test_csp_policy_map_matches(self) -> None:
        def csp(text: str) -> str:
            m = re.search(r"map \$uri \$csp_policy \{(.*?)\n {4}\}", text, re.S)
            assert m, "csp_policy map not found"
            return re.sub(r"\s+", " ", _strip_comments(m.group(1))).strip()

        assert csp(_read("docker")) == csp(_read("k8s")), "docker/k8s $csp_policy maps drifted"


class TestK8sConfigMapActuallyReachesThePod:
    """A ConfigMap-only change must roll the nginx pod.

    Two independent silent-no-op paths existed in the chart:
      1. No `checksum/config`-style annotation, so a `helm upgrade` that only
         changed files/nginx.conf produced a byte-identical Deployment spec —
         no new pod template, no rollout, `helm upgrade` reports success.
      2. The ConfigMap is mounted with `subPath`, which kubelet never updates
         in place, so even a long-lived pod would keep serving the config it
         was created with.
    Together they meant shipping an nginx fix to staging changed nothing
    unless someone remembered `kubectl rollout restart` by hand.
    """

    DEPLOYMENT = REPO_ROOT / "k8s" / "charts" / "nova" / "templates" / "deployment-nginx.yaml"

    def test_pod_template_has_a_config_checksum_annotation(self) -> None:
        text = self.DEPLOYMENT.read_text(encoding="utf-8")
        assert "checksum/nginx-config:" in text, "nginx pod will not roll when files/nginx.conf changes"
        assert "configmap-nginx.yaml" in text, "the checksum must be computed from the nginx ConfigMap template"
        assert "sha256sum" in text


VOICE_LOCATION = "location ~ ^/api/voice/session/"


class TestVoiceWebSocketLocation:
    """The voice session is a WebSocket and needs its own nginx location.

    The generic `location /api/` sets no `Upgrade` header, so the handshake
    400s behind nginx while working perfectly under `make dev`, which bypasses
    nginx entirely. That is the exact shape of the bug that blanked the Browser
    tab for a release, so it is pinned here before it can happen twice.
    """

    @pytest.mark.parametrize("name", ALL)
    def test_location_exists(self, name: str) -> None:
        assert VOICE_LOCATION in _read(name), f"{name}: voice WebSocket has no location — the handshake will fail behind nginx"

    @pytest.mark.parametrize("name", ALL)
    def test_supports_websocket_upgrade(self, name: str) -> None:
        body = _strip_comments(_location_body(_read(name), VOICE_LOCATION))
        assert "proxy_set_header Upgrade $http_upgrade;" in body, f"{name}: voice socket cannot upgrade"
        assert "proxy_set_header Connection $connection_upgrade;" in body, f"{name}: Connection must come from the $connection_upgrade map"

    @pytest.mark.parametrize("name", ALL)
    def test_read_timeout_survives_a_quiet_conversation(self, name: str) -> None:
        """A voice session is idle between utterances by design."""
        body = _strip_comments(_location_body(_read(name), VOICE_LOCATION))
        assert "proxy_read_timeout" in body, f"{name}: nginx's 60s default would drop the mic mid-conversation"


class TestMicrophonePermission:
    """`Permissions-Policy: microphone=()` disables getUserMedia app-wide.

    It was set that way, so voice capture could never work on any deployment —
    and only there, since local dev serves no headers at all. `(self)` permits
    our own origin while still blocking every embedded third party.
    """

    @pytest.mark.parametrize("name", HEADERED)
    def test_microphone_allowed_for_self(self, name: str) -> None:
        text = _read(name)
        assert "microphone=(self)" in text, f"{name}: microphone is blocked app-wide; voice capture cannot work"
        assert "microphone=()" not in text, f"{name}: a fully-disabling microphone policy is still present"

    @pytest.mark.parametrize("name", HEADERED)
    def test_camera_and_geolocation_stay_disabled(self, name: str) -> None:
        """Relaxing the mic must not relax anything else."""
        text = _read(name)
        assert "camera=()" in text, f"{name}: camera should remain disabled"
        assert "geolocation=()" in text, f"{name}: geolocation should remain disabled"
