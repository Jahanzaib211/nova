"""Browser layer API: profiles (per user) and per-thread browser state/recordings.

Profiles (`/api/browser/profiles`) are scoped to the authenticated caller —
there is no user id in the path, so one user can never address another's.
Only secret-free metadata is returned; cookie values never leave the store.

Thread endpoints (`/api/threads/{id}/browser/...`) are owner-checked with the
same `require_permission(..., owner_check=True)` as artifacts, and resolve
every path through `nova.config.paths` plus strict id/name patterns, so a
session id or frame name can never walk out of the recordings directory.
Recordings and profiles are not part of the public share view (which returns
messages only) and live in dot-directories the workspace zip skips.
"""

from __future__ import annotations

import asyncio
import shutil

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.gateway.authz import require_permission
from app.gateway.deps import get_current_user_from_request
from nova.browser import recording, thread_state
from nova.browser.profiles import ProfileError, ProfileStore, validate_name
from nova.config.browser_config import get_browser_config
from nova.config.paths import get_paths
from nova.runtime.user_context import get_effective_user_id

router = APIRouter(prefix="/api", tags=["browser"])

_META_KEYS = ("name", "domains", "cookie_count", "origin_count", "bytes", "created_at", "updated_at", "is_default", "corrupt")


class BrowserProfileInfo(BaseModel):
    name: str
    domains: list[str]
    cookie_count: int = 0
    origin_count: int = 0
    bytes: int = 0
    created_at: float | None = None
    updated_at: float | None = None
    is_default: bool = False
    corrupt: bool = False


class BrowserProfilesResponse(BaseModel):
    enabled: bool
    profiles: list[BrowserProfileInfo]


class MessageResponse(BaseModel):
    message: str


class RecordingToggle(BaseModel):
    enabled: bool


class ThreadBrowserState(BaseModel):
    recording: bool
    recording_default: bool
    profile: str | None = None
    attention: dict | None = None


async def _store(request: Request) -> ProfileStore:
    user = await get_current_user_from_request(request)
    return ProfileStore(str(user.id))


@router.get("/browser/profiles", response_model=BrowserProfilesResponse)
async def list_browser_profiles(request: Request) -> BrowserProfilesResponse:
    """The caller's saved browser profiles (metadata only — never cookie values)."""
    store = await _store(request)
    rows = await asyncio.to_thread(store.list)
    return BrowserProfilesResponse(enabled=get_browser_config().profiles.enabled, profiles=[BrowserProfileInfo(**{k: r.get(k) for k in _META_KEYS if r.get(k) is not None}) for r in rows])


@router.delete("/browser/profiles/{name}", response_model=MessageResponse)
async def delete_browser_profile(name: str, request: Request) -> MessageResponse:
    store = await _store(request)
    try:
        validate_name(name)
    except ProfileError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not await asyncio.to_thread(store.delete, name):
        raise HTTPException(status_code=404, detail="Profile not found")
    return MessageResponse(message="Profile deleted")


@router.post("/browser/profiles/{name}/default", response_model=MessageResponse)
async def set_default_browser_profile(name: str, request: Request) -> MessageResponse:
    store = await _store(request)
    try:
        validate_name(name)
        await asyncio.to_thread(store.set_default, name)
    except ProfileError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return MessageResponse(message="Default profile set")


@router.delete("/browser/profiles-default", response_model=MessageResponse)
async def clear_default_browser_profile(request: Request) -> MessageResponse:
    store = await _store(request)
    await asyncio.to_thread(store.set_default, None)
    return MessageResponse(message="Default profile cleared")


# ── per-thread ───────────────────────────────────────────────────────────────


def _dirs(thread_id: str):
    paths = get_paths()
    uid = get_effective_user_id()
    try:
        return paths.thread_dir(thread_id, user_id=uid), paths.sandbox_outputs_dir(thread_id, user_id=uid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/threads/{thread_id}/browser/state", response_model=ThreadBrowserState)
@require_permission("threads", "read", owner_check=True)
async def get_thread_browser_state(thread_id: str, request: Request) -> ThreadBrowserState:
    return _state(thread_id)


def _state(thread_id: str) -> ThreadBrowserState:
    tdir, _ = _dirs(thread_id)
    settings = thread_state.read_settings(tdir)
    default = get_browser_config().recording.enabled
    rec = settings.get("recording")
    return ThreadBrowserState(recording=default if rec is None else bool(rec), recording_default=default, profile=settings.get("profile"), attention=thread_state.read_attention(tdir))


@router.put("/threads/{thread_id}/browser/recording", response_model=ThreadBrowserState)
@require_permission("threads", "write", owner_check=True)
async def set_thread_recording(thread_id: str, body: RecordingToggle, request: Request) -> ThreadBrowserState:
    tdir, _ = _dirs(thread_id)
    await asyncio.to_thread(thread_state.update_settings, tdir, recording=body.enabled)
    return _state(thread_id)


@router.post("/threads/{thread_id}/browser/attention/resolve", response_model=MessageResponse)
@require_permission("threads", "write", owner_check=True)
async def resolve_thread_attention(thread_id: str, request: Request) -> MessageResponse:
    """The user says they finished the captcha/login in the live view."""
    tdir, _ = _dirs(thread_id)
    if await asyncio.to_thread(thread_state.resolve_attention, tdir) is None:
        raise HTTPException(status_code=404, detail="Nothing is waiting for you")
    return MessageResponse(message="Marked done; the agent will continue")


@router.get("/threads/{thread_id}/browser/recordings")
@require_permission("threads", "read", owner_check=True)
async def list_thread_recordings(thread_id: str, request: Request) -> dict:
    _, outputs = _dirs(thread_id)
    return {"recordings": await asyncio.to_thread(recording.list_sessions, outputs)}


@router.get("/threads/{thread_id}/browser/recordings/{session_id}")
@require_permission("threads", "read", owner_check=True)
async def get_thread_recording(thread_id: str, session_id: str, request: Request) -> dict:
    _, outputs = _dirs(thread_id)
    if not recording.valid_session_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid recording id")
    manifest = await asyncio.to_thread(recording.read_manifest, outputs, session_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    return manifest


@router.get("/threads/{thread_id}/browser/recordings/{session_id}/frames/{frame}")
@require_permission("threads", "read", owner_check=True)
async def get_thread_recording_frame(thread_id: str, session_id: str, frame: str, request: Request) -> FileResponse:
    _, outputs = _dirs(thread_id)
    if not (recording.valid_session_id(session_id) and recording.valid_frame_name(frame)):
        raise HTTPException(status_code=400, detail="Invalid frame")
    path = recording.frame_path(outputs, session_id, frame)
    if path is None:
        raise HTTPException(status_code=404, detail="Frame not found")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.delete("/threads/{thread_id}/browser/recordings/{session_id}", response_model=MessageResponse)
@require_permission("threads", "write", owner_check=True)
async def delete_thread_recording(thread_id: str, session_id: str, request: Request) -> MessageResponse:
    _, outputs = _dirs(thread_id)
    if not recording.valid_session_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid recording id")
    target = recording.recordings_dir(outputs) / session_id
    if not target.is_dir():
        raise HTTPException(status_code=404, detail="Recording not found")
    await asyncio.to_thread(shutil.rmtree, target, True)
    return MessageResponse(message="Recording deleted")
