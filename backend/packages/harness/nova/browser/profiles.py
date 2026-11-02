"""Persistent browser profiles: per-user, named, domain-scoped, encrypted at rest.

A profile is a Playwright ``storage_state`` (cookies + localStorage origins)
restricted to the domains it was saved for. It lives under the user's bucket::

    {base_dir}/users/{user_id}/browser_profiles/{name}.meta.json   # no secrets
    {base_dir}/users/{user_id}/browser_profiles/{name}.state.enc   # Fernet token

The metadata file holds only what the Settings list needs (domains, counts,
timestamps, default flag) so listing never decrypts anything. Cookie *values*
exist in plaintext only in memory, never in logs, metadata or recordings.

Encryption reuses the channel-credential cipher (Fernet keyed by SHA-256 of a
secret). The secret is ``NOVA_BROWSER_PROFILE_SECRET`` when set; otherwise a
random key persisted once at ``{base_dir}/.browser_profile_secret`` (0600), the
same bootstrap the JWT secret uses. Rotating the secret makes old profiles
unreadable — they surface as ``corrupt`` and can be deleted, never crash a tool.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from nova.config.browser_config import BrowserProfilesConfig, get_browser_config

logger = logging.getLogger(__name__)

PROFILE_SECRET_ENV = "NOVA_BROWSER_PROFILE_SECRET"
_SECRET_FILE = ".browser_profile_secret"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")
_MAX_DOMAINS = 50
_lock = threading.RLock()


class ProfileError(ValueError):
    """A user-facing profile problem (bad name, unknown profile, over a cap)."""


# ── domain logic ─────────────────────────────────────────────────────────────


def normalize_domain(raw: str) -> str:
    """``https://Sub.Example.com:443/x`` / ``.example.com`` → ``sub.example.com``."""
    value = (raw or "").strip().lower()
    if "://" in value:
        value = urlparse(value).hostname or ""
    value = value.split("/")[0].split(":")[0].lstrip(".").rstrip(".")
    if not _DOMAIN_RE.match(value):
        raise ProfileError(f"invalid domain {raw!r}")
    return value


def host_matches(host: str, domain: str) -> bool:
    """True when ``host`` is ``domain`` or one of its subdomains."""
    host = (host or "").lower().rstrip(".")
    domain = domain.lower().lstrip(".")
    return bool(host) and (host == domain or host.endswith("." + domain))


def _cookie_host(cookie: dict[str, Any]) -> str:
    return str(cookie.get("domain") or "").lower().lstrip(".")


def cookie_in_scope(cookie: dict[str, Any], domains: list[str]) -> bool:
    """A cookie belongs to a profile scoped to ``domains`` when either side contains the other.

    ``accounts.google.com`` scope keeps a ``.google.com`` cookie (the site reads
    it), and ``google.com`` scope keeps a ``mail.google.com`` cookie.
    """
    ch = _cookie_host(cookie)
    return bool(ch) and any(host_matches(ch, d) or host_matches(d, ch) for d in domains)


def cookie_applies_to(cookie: dict[str, Any], url: str, *, now: float | None = None) -> bool:
    """RFC 6265 domain/path/secure/expiry match of one stored cookie against a URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    ch = _cookie_host(cookie)
    if not host or not ch:
        return False
    # Playwright writes host-only cookies without the leading dot, so after a
    # storage_state round-trip both forms are treated as domain cookies — the
    # same thing the browser itself does once they are re-added.
    if not host_matches(host, ch):
        return False
    path = parsed.path or "/"
    cpath = str(cookie.get("path") or "/")
    if not (path == cpath or path.startswith(cpath.rstrip("/") + "/") or cpath == "/"):
        return False
    if cookie.get("secure") and parsed.scheme != "https":
        return False
    expires = cookie.get("expires")
    if isinstance(expires, (int, float)) and expires > 0 and expires < (now or time.time()):
        return False
    return True


def scope_storage_state(state: dict[str, Any], domains: list[str]) -> dict[str, Any]:
    """Keep only cookies/origins that belong to ``domains``."""
    cookies = [c for c in (state.get("cookies") or []) if isinstance(c, dict) and cookie_in_scope(c, domains)]
    origins = []
    for o in state.get("origins") or []:
        if not isinstance(o, dict):
            continue
        host = urlparse(str(o.get("origin") or "")).hostname or ""
        if any(host_matches(host, d) for d in domains):
            origins.append({"origin": o.get("origin"), "localStorage": list(o.get("localStorage") or [])})
    return {"cookies": cookies, "origins": origins}


def derive_domains(state: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for c in state.get("cookies") or []:
        h = _cookie_host(c) if isinstance(c, dict) else ""
        if h and h not in seen:
            seen.append(h)
    for o in state.get("origins") or []:
        h = urlparse(str((o or {}).get("origin") or "")).hostname or ""
        if h and h not in seen:
            seen.append(h)
    return seen[:_MAX_DOMAINS]


def cookie_header(cookies: list[dict[str, Any]]) -> str:
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))


def redact_cookies(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cookie list safe for logs/manifests: names and domains only."""
    return [{"name": c.get("name"), "domain": c.get("domain")} for c in cookies]


# ── encryption ───────────────────────────────────────────────────────────────


def _base_dir() -> Path:
    from nova.config.paths import get_paths

    return get_paths().base_dir


def _load_secret(base_dir: Path) -> str:
    env = os.environ.get(PROFILE_SECRET_ENV, "").strip()
    if env:
        return env
    path = base_dir / _SECRET_FILE
    with _lock:
        try:
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except FileNotFoundError:
            pass
        secret = secrets.token_urlsafe(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(secret)
        logger.info("Generated a browser-profile encryption key (set %s to manage it explicitly)", PROFILE_SECRET_ENV)
        return secret


def _cipher(base_dir: Path):
    # Same cipher the IM channel credentials use: one encryption idiom per repo.
    from nova.persistence.channel_connections.sql import ChannelCredentialCipher

    return ChannelCredentialCipher.from_key(_load_secret(base_dir))


def _atomic_write(path: Path, data: str) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(data)
    os.replace(tmp, path)


# ── store ────────────────────────────────────────────────────────────────────


@dataclass
class StoredProfile:
    name: str
    domains: list[str]
    state: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)

    def cookies_for(self, url: str) -> list[dict[str, Any]]:
        host = urlparse(url).hostname or ""
        if not any(host_matches(host, d) for d in self.domains):
            return []
        return [c for c in self.state.get("cookies") or [] if cookie_applies_to(c, url)]

    def origins_for(self, url: str) -> list[dict[str, Any]]:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        return [o for o in self.state.get("origins") or [] if o.get("origin") == origin]


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise ProfileError("profile name must be 1-64 chars of letters, digits, '.', '_' or '-' and start with a letter or digit")
    return name


class ProfileStore:
    """Profiles of one user. All methods are sync and short; callers on an event loop use ``asyncio.to_thread``."""

    def __init__(self, user_id: str, *, base_dir: Path | None = None, config: BrowserProfilesConfig | None = None) -> None:
        from nova.config.paths import make_safe_user_id

        self._base = Path(base_dir) if base_dir is not None else _base_dir()
        self.user_id = make_safe_user_id(str(user_id))
        self._cfg = config or get_browser_config().profiles
        self.dir = self._base / "users" / self.user_id / "browser_profiles"

    def _meta_path(self, name: str) -> Path:
        return self.dir / f"{validate_name(name)}.meta.json"

    def _state_path(self, name: str) -> Path:
        return self.dir / f"{validate_name(name)}.state.enc"

    def _read_meta(self, path: Path) -> dict[str, Any] | None:
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            return meta if isinstance(meta, dict) else None
        except (OSError, ValueError):
            return None

    def list(self) -> list[dict[str, Any]]:
        if not self.dir.is_dir():
            return []
        out = []
        for p in sorted(self.dir.glob("*.meta.json")):
            meta = self._read_meta(p)
            if meta and _NAME_RE.match(str(meta.get("name", ""))):
                meta["corrupt"] = not (self.dir / f"{meta['name']}.state.enc").is_file()
                out.append(meta)
        return out

    def exists(self, name: str) -> bool:
        return self._meta_path(name).is_file()

    def save(
        self,
        name: str,
        storage_state: dict[str, Any],
        domains: list[str] | None = None,
        *,
        make_default: bool = False,
        proxy_server: str = "",
    ) -> dict[str, Any]:
        name = validate_name(name)
        if not self._cfg.enabled:
            raise ProfileError("browser profiles are disabled on this instance (browser.profiles.enabled)")
        doms = [normalize_domain(d) for d in (domains or []) if str(d).strip()]
        if not doms:
            doms = derive_domains(storage_state)
        doms = list(dict.fromkeys(doms))[:_MAX_DOMAINS]
        if not doms:
            raise ProfileError("nothing to save: the browser has no cookies or localStorage yet (log in first, or pass domains)")
        scoped = scope_storage_state(storage_state, doms)
        if not scoped["cookies"] and not scoped["origins"]:
            raise ProfileError(f"no cookies or localStorage belong to {', '.join(doms)} — log in to the site first")
        payload = json.dumps(scoped, separators=(",", ":"))
        if len(payload) > self._cfg.max_bytes:
            raise ProfileError(f"profile is {len(payload):,} bytes, over the {self._cfg.max_bytes:,}-byte cap — narrow the domains")
        with _lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.dir, 0o700)
            if not self.exists(name) and len(self.list()) >= self._cfg.max_profiles:
                raise ProfileError(f"profile limit reached ({self._cfg.max_profiles}); delete one first")
            previous = self._read_meta(self._meta_path(name)) or {}
            now = time.time()
            meta = {
                "name": name,
                "domains": doms,
                "cookie_count": len(scoped["cookies"]),
                "origin_count": len(scoped["origins"]),
                "bytes": len(payload),
                "created_at": previous.get("created_at", now),
                "updated_at": now,
                "is_default": bool(make_default or previous.get("is_default")),
                "proxy_server": proxy_server or previous.get("proxy_server", ""),
            }
            _atomic_write(self._state_path(name), _cipher(self._base).encrypt_text(payload) or "")
            _atomic_write(self._meta_path(name), json.dumps(meta))
            if make_default:
                self.set_default(name)
        logger.info("Saved browser profile %r for user %s (%d cookies, %d origins, domains=%s)", name, self.user_id, meta["cookie_count"], meta["origin_count"], doms)
        return meta

    def load(self, name: str) -> StoredProfile:
        name = validate_name(name)
        meta = self._read_meta(self._meta_path(name))
        if meta is None:
            raise ProfileError(f"no browser profile named {name!r}")
        try:
            token = self._state_path(name).read_text(encoding="utf-8")
            state = json.loads(_cipher(self._base).decrypt_text(token) or "{}")
        except Exception as exc:  # noqa: BLE001 - InvalidToken, IO, JSON: all "unreadable"
            raise ProfileError(f"browser profile {name!r} could not be decrypted (key rotated?) — delete and save it again") from exc
        return StoredProfile(name=name, domains=list(meta.get("domains") or []), state=state, meta=meta)

    def delete(self, name: str) -> bool:
        name = validate_name(name)
        with _lock:
            found = False
            for p in (self._meta_path(name), self._state_path(name)):
                try:
                    p.unlink()
                    found = True
                except FileNotFoundError:
                    pass
        return found

    def set_default(self, name: str | None) -> None:
        with _lock:
            for meta in self.list():
                want = name is not None and meta["name"] == name
                if bool(meta.get("is_default")) != want:
                    meta.pop("corrupt", None)
                    meta["is_default"] = want
                    _atomic_write(self._meta_path(meta["name"]), json.dumps(meta))
            if name is not None and not self.exists(name):
                raise ProfileError(f"no browser profile named {name!r}")

    def default_name(self) -> str | None:
        for meta in self.list():
            if meta.get("is_default") and not meta.get("corrupt"):
                return meta["name"]
        return None

    def match_url(self, url: str) -> StoredProfile | None:
        """The profile whose domains cover ``url`` (default profile first, then most specific)."""
        host = urlparse(url).hostname or ""
        if not host:
            return None
        candidates = [m for m in self.list() if not m.get("corrupt") and any(host_matches(host, d) for d in m.get("domains") or [])]
        if not candidates:
            return None

        def rank(m: dict[str, Any]) -> tuple[int, int]:
            best = max((len(d) for d in m.get("domains") or [] if host_matches(host, d)), default=0)
            return (1 if m.get("is_default") else 0, best)

        for meta in sorted(candidates, key=rank, reverse=True):
            try:
                return self.load(meta["name"])
            except ProfileError:
                continue
        return None


def cookies_for_fetch(url: str, user_id: str | None, *, use_profile: bool = True) -> list[dict[str, Any]]:
    """Cookies a fetch engine should send for ``url`` on behalf of ``user_id``. Never raises."""
    if not use_profile or not user_id:
        return []
    try:
        cfg = get_browser_config().profiles
        if not (cfg.enabled and cfg.inject_into_fetch):
            return []
        profile = ProfileStore(user_id, config=cfg).match_url(url)
        return profile.cookies_for(url) if profile else []
    except Exception:  # noqa: BLE001 - a profile problem must never break a fetch
        logger.debug("profile cookie lookup failed", exc_info=True)
        return []
