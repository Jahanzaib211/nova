"""SandboxAuditMiddleware - bash command security auditing."""

import json
import logging
import re
import shlex
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from deerflow.agents.thread_state import ThreadState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Command classification rules
# ---------------------------------------------------------------------------

# Each pattern is compiled once at import time.
_HIGH_RISK_PATTERNS: list[re.Pattern[str]] = [
    # --- original rules (retained) ---
    re.compile(r"rm\s+-[^\s]*r[^\s]*\s+(/\*?|~/?\*?|/home\b|/root\b)\s*$"),
    re.compile(r"dd\s+if="),
    re.compile(r"mkfs"),
    re.compile(r"cat\s+/etc/shadow"),
    re.compile(r">+\s*/etc/"),
    # --- pipe to sh/bash (generalised, replaces old curl|sh rule) ---
    re.compile(r"\|\s*(ba)?sh\b"),
    # --- command substitution (targeted – only dangerous executables) ---
    re.compile(r"[`$]\(?\s*(curl|wget|bash|sh|python|ruby|perl|base64)"),
    # --- base64 decode piped to execution ---
    re.compile(r"base64\s+.*-d.*\|"),
    # --- overwrite system binaries ---
    re.compile(r">+\s*(/usr/bin/|/bin/|/sbin/)"),
    # --- overwrite shell startup files ---
    re.compile(r">+\s*~/?\.(bashrc|profile|zshrc|bash_profile)"),
    # --- process environment leakage ---
    re.compile(r"/proc/[^/]+/environ"),
    # --- dynamic linker hijack (one-step escalation) ---
    re.compile(r"\b(LD_PRELOAD|LD_LIBRARY_PATH)\s*="),
    # --- bash built-in networking (bypasses tool allowlists) ---
    re.compile(r"/dev/tcp/"),
    # --- fork bomb ---
    re.compile(r"\S+\(\)\s*\{[^}]*\|\s*\S+\s*&"),  # :(){ :|:& };:
    re.compile(r"while\s+true.*&\s*done"),  # while true; do bash & done
]

_MEDIUM_RISK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"chmod\s+777"),
    re.compile(r"pip3?\s+install"),
    re.compile(r"apt(-get)?\s+install"),
    # sudo/su: no-op under Docker root; warn so LLM is aware
    re.compile(r"\b(sudo|su)\b"),
    # PATH modification: long attack chain, warn rather than block
    re.compile(r"\bPATH\s*="),
]

# ---------------------------------------------------------------------------
# Harness checks (additive, non-blocking — append warnings only)
# ---------------------------------------------------------------------------

# Secret-leak patterns scanned on write_file content.
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OpenAI key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("generic secret assignment", re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]")),
]

# A small set of frequently-typosquatted popular npm packages.
_POPULAR_NPM = {
    "react",
    "react-dom",
    "next",
    "vue",
    "express",
    "lodash",
    "axios",
    "chalk",
    "commander",
    "typescript",
    "tailwindcss",
    "vite",
    "webpack",
    "eslint",
    "prettier",
    "zod",
    "prisma",
    "dotenv",
    "cors",
    "jsonwebtoken",
    "bcrypt",
    "nodemon",
    "ts-node",
    "uuid",
    "moment",
    "dayjs",
    "classnames",
    "clsx",
    "framer-motion",
    "zustand",
    "redux",
    "@types/node",
    "@types/react",
}

_INSTALL_RE = re.compile(r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:add|install|i)\b([^&|;]*)")


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _scan_secrets(content: str) -> list[str]:
    """Return a list of secret-type labels found in *content* (best-effort)."""
    found: list[str] = []
    for label, pat in _SECRET_PATTERNS:
        if pat.search(content):
            found.append(label)
    return found


def _check_slopsquat(command: str) -> list[str]:
    """Return warnings for npm install targets that look like typosquats."""
    warnings: list[str] = []
    for m in _INSTALL_RE.finditer(command):
        pkgs = m.group(1).split()
        for raw in pkgs:
            if raw.startswith("-") or raw.startswith("$"):
                continue  # flag / variable, not a package
            pkg = raw.strip().strip("'\"")
            # strip version spec and scope for comparison
            base = pkg.split("@")[0] if not pkg.startswith("@") else pkg
            if not base or base in _POPULAR_NPM:
                continue
            low = base.lower()
            for popular in _POPULAR_NPM:
                if popular.startswith("@") or len(popular) < 4:
                    continue
                d = _levenshtein(low, popular)
                # d<=2 catches single transpositions (reqeusts↔requests) and
                # 1-char edits; length guard keeps false positives low. Advisory only.
                if 0 < d <= 2 and abs(len(base) - len(popular)) <= 1:
                    warnings.append(f"`{pkg}` is suspiciously close to the popular package `{popular}` — possible typosquat. Verify the name before installing.")
                    break
    return warnings


def _split_compound_command(command: str) -> list[str]:
    """Split a compound command into sub-commands (quote-aware).

    Scans the raw command string so unquoted shell control operators are
    recognised even when they are not surrounded by whitespace
    (e.g. ``safe;rm -rf /`` or ``rm -rf /&&echo ok``). Operators inside
    quotes are ignored. If the command ends with an unclosed quote or a
    dangling escape, return the whole command unchanged (fail-closed —
    safer to classify the unsplit string than silently drop parts).
    """
    parts: list[str] = []
    current: list[str] = []
    in_single_quote = False
    in_double_quote = False
    escaping = False
    index = 0

    while index < len(command):
        char = command[index]

        if escaping:
            current.append(char)
            escaping = False
            index += 1
            continue

        if char == "\\" and not in_single_quote:
            current.append(char)
            escaping = True
            index += 1
            continue

        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            current.append(char)
            index += 1
            continue

        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            current.append(char)
            index += 1
            continue

        if not in_single_quote and not in_double_quote:
            if command.startswith("&&", index) or command.startswith("||", index):
                part = "".join(current).strip()
                if part:
                    parts.append(part)
                current = []
                index += 2
                continue
            if char == ";":
                part = "".join(current).strip()
                if part:
                    parts.append(part)
                current = []
                index += 1
                continue

        current.append(char)
        index += 1

    # Unclosed quote or dangling escape → fail-closed, return whole command
    if in_single_quote or in_double_quote or escaping:
        return [command]

    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts if parts else [command]


def _classify_single_command(command: str) -> str:
    """Classify a single (non-compound) command. Return 'block', 'warn', or 'pass'."""
    normalized = " ".join(command.split())

    for pattern in _HIGH_RISK_PATTERNS:
        if pattern.search(normalized):
            return "block"

    # Also try shlex-parsed tokens for high-risk detection
    try:
        tokens = shlex.split(command)
        joined = " ".join(tokens)
        for pattern in _HIGH_RISK_PATTERNS:
            if pattern.search(joined):
                return "block"
    except ValueError:
        # shlex.split fails on unclosed quotes — treat as suspicious
        return "block"

    for pattern in _MEDIUM_RISK_PATTERNS:
        if pattern.search(normalized):
            return "warn"

    return "pass"


def _classify_command(command: str) -> str:
    """Return 'block', 'warn', or 'pass'.

    Strategy:
    1. First scan the *whole* raw command against high-risk patterns. This
       catches structural attacks like ``while true; do bash & done`` or
       ``:(){ :|:& };:`` that span multiple shell statements — splitting them
       on ``;`` would destroy the pattern context.
    2. Then split compound commands (e.g. ``cmd1 && cmd2 ; cmd3``) and
       classify each sub-command independently. The most severe verdict wins.
    """
    # Pass 1: whole-command high-risk scan (catches multi-statement patterns)
    normalized = " ".join(command.split())
    for pattern in _HIGH_RISK_PATTERNS:
        if pattern.search(normalized):
            return "block"

    # Pass 2: per-sub-command classification
    sub_commands = _split_compound_command(command)
    worst = "pass"
    for sub in sub_commands:
        verdict = _classify_single_command(sub)
        if verdict == "block":
            return "block"  # short-circuit: can't get worse
        if verdict == "warn":
            worst = "warn"
    return worst


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class SandboxAuditMiddleware(AgentMiddleware[ThreadState]):
    """Bash command security auditing middleware.

    For every ``bash`` tool call:
    1. **Command classification**: regex + shlex analysis grades commands as
       high-risk (block), medium-risk (warn), or safe (pass).
    2. **Audit log**: every bash call is recorded as a structured JSON entry
       via the standard logger (visible in langgraph.log).

    High-risk commands (e.g. ``rm -rf /``, ``curl url | bash``) are blocked:
    the handler is not called and an error ``ToolMessage`` is returned so the
    agent loop can continue gracefully.

    Medium-risk commands (e.g. ``pip install``, ``chmod 777``) are executed
    normally; a warning is appended to the tool result so the LLM is aware.
    """

    state_schema = ThreadState

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_thread_id(self, request: ToolCallRequest) -> str | None:
        runtime = request.runtime  # ToolRuntime; may be None-like in tests
        if runtime is None:
            return None
        ctx = getattr(runtime, "context", None) or {}
        thread_id = ctx.get("thread_id") if isinstance(ctx, dict) else None
        if thread_id is None:
            cfg = getattr(runtime, "config", None) or {}
            thread_id = cfg.get("configurable", {}).get("thread_id")
        return thread_id

    _AUDIT_COMMAND_LIMIT = 200

    def _write_audit(self, thread_id: str | None, command: str, verdict: str, *, truncate: bool = False) -> None:
        audited_command = command
        if truncate and len(command) > self._AUDIT_COMMAND_LIMIT:
            audited_command = f"{command[: self._AUDIT_COMMAND_LIMIT]}... ({len(command)} chars)"
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "thread_id": thread_id or "unknown",
            "command": audited_command,
            "verdict": verdict,
        }
        logger.info("[SandboxAudit] %s", json.dumps(record, ensure_ascii=False))

    def _build_block_message(self, request: ToolCallRequest, reason: str) -> ToolMessage:
        tool_call_id = str(request.tool_call.get("id") or "missing_id")
        return ToolMessage(
            content=f"Command blocked: {reason}. Please use a safer alternative approach.",
            tool_call_id=tool_call_id,
            name="bash",
            status="error",
        )

    def _append_warn_to_result(self, result: ToolMessage | Command, command: str) -> ToolMessage | Command:
        """Append a warning note to the tool result for medium-risk commands."""
        return self._append_note(result, f"\n\n⚠️ Warning: `{command}` is a medium-risk command that may modify the runtime environment.")

    def _append_note(self, result: ToolMessage | Command, note: str) -> ToolMessage | Command:
        """Append an arbitrary advisory note to a ToolMessage result (non-blocking)."""
        if not isinstance(result, ToolMessage):
            return result
        if isinstance(result.content, list):
            new_content = list(result.content) + [{"type": "text", "text": note}]
        else:
            new_content = str(result.content) + note
        return ToolMessage(
            content=new_content,
            tool_call_id=result.tool_call_id,
            name=result.name,
            status=result.status,
        )

    def _harness_pre_note(self, request: ToolCallRequest) -> str | None:
        """Harness pre-execution advisories (non-blocking). Returns a note or None."""
        name = request.tool_call.get("name")
        args = request.tool_call.get("args", {}) or {}
        notes: list[str] = []

        if name == "write_file":
            content = args.get("content")
            if isinstance(content, str) and content:
                leaks = _scan_secrets(content)
                if leaks:
                    notes.append("🔐 Secret-scan: this file appears to contain " + ", ".join(leaks) + ". Use environment variables / .env (gitignored) instead of hard-coding secrets.")
        elif name == "bash":
            cmd = args.get("command")
            if isinstance(cmd, str) and cmd:
                for w in _check_slopsquat(cmd):
                    notes.append("📦 Slopsquatting: " + w)
        return "\n\n".join(notes) if notes else None

    # ------------------------------------------------------------------
    # Input sanitisation
    # ------------------------------------------------------------------

    # Normal bash commands rarely exceed a few hundred characters.  10 000 is
    # well above any legitimate use case yet a tiny fraction of Linux ARG_MAX.
    # Anything longer is almost certainly a payload injection or base64-encoded
    # attack string.
    _MAX_COMMAND_LENGTH = 10_000

    def _validate_input(self, command: str) -> str | None:
        """Return ``None`` if *command* is acceptable, else a rejection reason."""
        if not command.strip():
            return "empty command"
        if len(command) > self._MAX_COMMAND_LENGTH:
            return "command too long"
        if "\x00" in command:
            return "null byte detected"
        return None

    # ------------------------------------------------------------------
    # Core logic (shared between sync and async paths)
    # ------------------------------------------------------------------

    def _pre_process(self, request: ToolCallRequest) -> tuple[str, str | None, str, str | None]:
        """
        Returns (command, thread_id, verdict, reject_reason).
        verdict is 'block', 'warn', or 'pass'.
        reject_reason is non-None only for input sanitisation rejections.
        """
        args = request.tool_call.get("args", {})
        raw_command = args.get("command")
        command = raw_command if isinstance(raw_command, str) else ""
        thread_id = self._get_thread_id(request)

        # ① input sanitisation — reject malformed input before regex analysis
        reject_reason = self._validate_input(command)
        if reject_reason:
            self._write_audit(thread_id, command, "block", truncate=True)
            logger.warning("[SandboxAudit] INVALID INPUT thread=%s reason=%s", thread_id, reject_reason)
            return command, thread_id, "block", reject_reason

        # ② classify command
        verdict = _classify_command(command)

        # ③ audit log
        self._write_audit(thread_id, command, verdict)

        if verdict == "block":
            logger.warning("[SandboxAudit] BLOCKED thread=%s cmd=%r", thread_id, command)
        elif verdict == "warn":
            logger.warning("[SandboxAudit] WARN (medium-risk) thread=%s cmd=%r", thread_id, command)

        return command, thread_id, verdict, None

    # ------------------------------------------------------------------
    # wrap_tool_call hooks
    # ------------------------------------------------------------------

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        name = request.tool_call.get("name")
        if name != "bash":
            # Harness advisories for non-bash tools (e.g. write_file secret scan)
            harness_note = self._harness_pre_note(request)
            result = handler(request)
            return self._append_note(result, "\n\n" + harness_note) if harness_note else result

        harness_note = self._harness_pre_note(request)
        command, _, verdict, reject_reason = self._pre_process(request)
        if verdict == "block":
            reason = reject_reason or "security violation detected"
            return self._build_block_message(request, reason)
        result = handler(request)
        if verdict == "warn":
            result = self._append_warn_to_result(result, command)
        if harness_note:
            result = self._append_note(result, "\n\n" + harness_note)
        return result

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        name = request.tool_call.get("name")
        if name != "bash":
            harness_note = self._harness_pre_note(request)
            result = await handler(request)
            return self._append_note(result, "\n\n" + harness_note) if harness_note else result

        harness_note = self._harness_pre_note(request)
        command, _, verdict, reject_reason = self._pre_process(request)
        if verdict == "block":
            reason = reject_reason or "security violation detected"
            return self._build_block_message(request, reason)
        result = await handler(request)
        if verdict == "warn":
            result = self._append_warn_to_result(result, command)
        if harness_note:
            result = self._append_note(result, "\n\n" + harness_note)
        return result
