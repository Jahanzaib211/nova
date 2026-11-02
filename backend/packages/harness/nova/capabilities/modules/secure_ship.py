"""Secure-Ship: the gate a build passes before Nova hands it over.

Nova can write a whole app in one turn and hand it to the user with
``present_files`` or a live preview URL. Nothing on that path asked whether
what is about to be shipped is *safe* to ship: a hard-coded API key in
``config.py`` travels inside the delivered files, and the deliverable is
already public by the time anyone notices.

The gate engine (:mod:`deerflow.security.secure_ship`) scans a thread's
workspace and returns a verdict -- ``pass``, ``warn`` or ``block`` -- with the
findings behind it. This module is the registry's view of that engine: one
declaration, reachable from the lead agent, the UI and an external harness
over MCP, so a harness-driven build is gated exactly like a native one.

**Every operation is thread-scoped.** A gate run is about one thread's
workspace; without a thread id there is nothing to scan, so the call is
refused rather than landing on some other thread's files.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from deerflow.capabilities.modules._common import Empty
from deerflow.capabilities.types import CapabilityModule, ModuleStatus, OpContext, Operation

logger = logging.getLogger(__name__)

# The engine is imported defensively: the registry must come up even when the
# gate engine is unavailable, and an operation that cannot run says so (a
# ValueError, i.e. HTTP 422) instead of taking the whole surface down.
try:  # pragma: no cover - exercised through the module's own handlers
    from deerflow.security.secure_ship import default_policy, run_gate, toolkit_manifest
except Exception as _engine_import_error:  # noqa: BLE001 - any import failure is "engine missing"
    default_policy = None  # type: ignore[assignment]
    run_gate = None  # type: ignore[assignment]
    toolkit_manifest = None  # type: ignore[assignment]
    logger.debug("Secure-Ship gate engine unavailable: %s", _engine_import_error)


class ScanIn(BaseModel):
    """What to scan."""

    thread_id: str | None = Field(default=None, description="Thread whose build to scan. Defaults to the caller's own thread.")
    deep: bool = Field(default=False, description="Run the deeper (slower) scan as well.")


class ThreadScoped(BaseModel):
    """Inputs may name a thread explicitly; otherwise the context supplies it."""

    thread_id: str | None = Field(default=None, description="Thread to read from. Defaults to the caller's own thread.")


class ScanOut(BaseModel):
    report: dict = Field(description="The gate report: verdict, counts, findings and the policy that produced them.")


class ReportOut(BaseModel):
    report: dict | None = Field(default=None, description="The last known gate report for the thread, or null when the gate has never run.")


class ToolkitOut(BaseModel):
    toolkit: dict = Field(description="The security tooling available to the gate: scanners and what each one looks for.")


class PolicyOut(BaseModel):
    policy: dict = Field(description="The active gate policy: severities, thresholds and which scanners run.")


#: File names the engine may use for the persisted report, next to
#: ``SECURE-SHIP.md``. Tried in order; the glob below is the fallback.
_REPORT_FILENAMES: tuple[str, ...] = (
    "SECURE-SHIP.json",
    "SECURE-SHIP.report.json",
    "secure_ship.json",
    "secure_ship_report.json",
    "secure-ship-report.json",
    "secure-ship.json",
)


def _thread(ctx: OpContext, inp: ThreadScoped) -> str:
    """The thread to act in, or a refusal naming why there is none.

    An explicit ``thread_id`` wins so a caller can read across its own
    threads; otherwise the context's. A missing id is an error rather than a
    default, because "no thread" names no build to scan -- guessing would
    report on files the caller never asked about.
    """
    thread_id = (inp.thread_id or ctx.thread_id or "").strip()
    if not thread_id:
        raise ValueError("No thread in scope. Pass thread_id, or call with a per-turn runtime token (minted as runtime:<thread_id>) so the build to scan is unambiguous.")
    return thread_id


def _jsonable(value: Any) -> Any:
    """Coerce *value* into something ``model_dump(mode="json")`` accepts."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return str(value)


def _as_dict(value: Any) -> dict[str, Any]:
    """Best-effort dict view of a report / policy object."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return dict(model_dump(mode="json"))
        except Exception:  # noqa: BLE001 - fall through to the other shapes
            try:
                return dict(model_dump())
            except Exception:  # noqa: BLE001
                pass
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            out = to_dict()
        except Exception:  # noqa: BLE001
            out = None
        if isinstance(out, dict):
            return dict(out)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        try:
            return dataclasses.asdict(value)
        except Exception:  # noqa: BLE001
            pass
    try:
        return {k: v for k, v in vars(value).items() if not k.startswith("_")}
    except TypeError:
        return {"value": str(value)}


def _report_dict(report: Any) -> dict:
    """The report as a plain dict (the engine's own ``to_dict`` when present)."""
    to_dict = getattr(report, "to_dict", None)
    if callable(to_dict):
        try:
            out = to_dict()
            if isinstance(out, dict):
                return _jsonable(out)
        except Exception:  # noqa: BLE001 - fall back to the generic shape
            logger.debug("GateReport.to_dict() failed; using the generic shape", exc_info=True)
    return _jsonable(_as_dict(report))


def _workspace_dirs(thread_id: str, user_id: str | None) -> list[Path]:
    """Where the thread's build files live (workspace first, then outputs)."""
    try:
        from deerflow.config.paths import get_paths

        paths = get_paths()
        dirs = [paths.sandbox_work_dir(thread_id, user_id=user_id), paths.sandbox_outputs_dir(thread_id, user_id=user_id)]
        return [d for d in dirs]
    except Exception:  # noqa: BLE001 - an unreadable path config means "no report"
        logger.debug("Could not resolve the workspace for thread %s", thread_id, exc_info=True)
        return []


def _looks_like_a_report(payload: Any) -> bool:
    """Whether a JSON payload is a gate report (and not some other file)."""
    if not isinstance(payload, dict):
        return False
    if "verdict" not in payload:
        return False
    return any(key in payload for key in ("findings", "counts", "scanners", "thread_id"))


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _find_report_file(thread_id: str, user_id: str | None) -> Path | None:
    """The persisted report JSON the gate wrote next to ``SECURE-SHIP.md``.

    The engine's own name is tried first, then any json in the workspace
    whose name mentions the gate, then any json that actually parses as a
    gate report -- the last one is what keeps this working if the engine
    changes the file name it writes.
    """
    fallback: Path | None = None
    directories = [d for d in _workspace_dirs(thread_id, user_id) if d.is_dir()]
    # First pass: the names the engine is known to use.
    for directory in directories:
        for name in _REPORT_FILENAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
        try:
            others = sorted(p for p in directory.glob("*.json") if p.is_file())
        except OSError:
            continue
        for candidate in others:
            stem = candidate.stem.lower().replace("_", "-")
            if "secure" in stem and "ship" in stem:
                return candidate
        if fallback is None:
            for candidate in others:
                if _looks_like_a_report(_read_json(candidate)):
                    fallback = candidate
                    break
    return fallback


async def _scan(ctx: OpContext, inp: ScanIn) -> ScanOut:
    """Run the gate over the thread's build."""
    thread_id = _thread(ctx, inp)
    if run_gate is None:
        raise ValueError("Secure-Ship is unavailable: the gate engine could not be loaded.")
    try:
        report = await run_gate(thread_id, user_id=ctx.user_id, deep=inp.deep)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - a failed scan is a 422, never a 500
        raise ValueError(f"Secure-Ship scan failed for thread {thread_id}: {exc}") from exc
    return ScanOut(report=_report_dict(report))


async def _report(ctx: OpContext, inp: ThreadScoped) -> ReportOut:
    """The last report the gate wrote for this thread -- no scan."""
    thread_id = _thread(ctx, inp)
    path = _find_report_file(thread_id, ctx.user_id)
    if path is None:
        return ReportOut(report=None)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Secure-Ship report at %s is unreadable: %s", path, exc)
        return ReportOut(report=None)
    if not isinstance(payload, dict):
        return ReportOut(report={"report": _jsonable(payload)})
    return ReportOut(report=_jsonable(payload))


async def _toolkit(ctx: OpContext, inp: Empty) -> ToolkitOut:
    """Which security tooling the gate has."""
    if toolkit_manifest is None:
        raise ValueError("Secure-Ship is unavailable: the gate engine could not be loaded.")
    try:
        manifest = toolkit_manifest(ctx.thread_id)
    except Exception as exc:  # noqa: BLE001 - a failed probe is a 422, never a 500
        raise ValueError(f"Secure-Ship toolkit unavailable: {exc}") from exc
    return ToolkitOut(toolkit=_jsonable(_as_dict(manifest)))


async def _policy(ctx: OpContext, inp: Empty) -> PolicyOut:
    """The policy the gate is running under."""
    if default_policy is None:
        raise ValueError("Secure-Ship is unavailable: the gate engine could not be loaded.")
    try:
        policy = default_policy()
    except Exception as exc:  # noqa: BLE001 - a failed probe is a 422, never a 500
        raise ValueError(f"Secure-Ship policy unavailable: {exc}") from exc
    return PolicyOut(policy=_jsonable(_as_dict(policy)))


async def _status() -> ModuleStatus:
    """Cheap probe: is the gate engine loaded? (It carries its own health.)"""
    if run_gate is None:
        return ModuleStatus(configured=False, healthy=False, detail="gate engine unavailable")
    return ModuleStatus(configured=True, healthy=True, detail="gate engine available")


MODULE = CapabilityModule(
    id="secure_ship",
    title="Secure-Ship",
    description="The shipping gate: scan a thread's build for hard-coded secrets, unsafe dependencies and other release blockers before Nova hands it over.",
    status=_status,
    operations=[
        Operation(
            name="secure_ship.scan",
            kind="execute",
            input=ScanIn,
            output=ScanOut,
            handler=_scan,
            description="Run the Secure-Ship gate over a thread's build and return the report (verdict, counts, findings).",
            harness=True,
            mcp=True,
        ),
        Operation(
            name="secure_ship.report",
            kind="read",
            input=ThreadScoped,
            output=ReportOut,
            handler=_report,
            description="The last gate report written for a thread, or null when the gate has never run. No scan.",
            harness=True,
            mcp=True,
        ),
        Operation(
            name="secure_ship.toolkit",
            kind="read",
            input=Empty,
            output=ToolkitOut,
            handler=_toolkit,
            description="The security tooling the gate has: the scanners it runs and what each one looks for.",
            harness=True,
            mcp=True,
        ),
        Operation(
            name="secure_ship.policy",
            kind="read",
            input=Empty,
            output=PolicyOut,
            handler=_policy,
            description="The active gate policy: severities, thresholds and which scanners run.",
            harness=True,
            mcp=True,
        ),
    ],
)
