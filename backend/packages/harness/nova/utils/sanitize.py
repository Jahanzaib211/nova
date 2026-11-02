"""Make raw terminal bytes safe to render, without throwing away colour.

The Agent's Computer Terminal renders observation text into a ``<pre>``. React
escapes HTML there, which stops injection but does nothing about C0 control
bytes: they reach the browser and paint as garbage. Measured across 89 live
``sandbox.log`` files, 39 observation lines carried ANSI escapes and 3 carried
runs of NUL bytes -- all of them dev-server output mirrored into the log, and
all of them rendering as the corrupted glyph soup users reported.

**The policy is deliberately not "strip all ANSI".** Colour is the half worth
keeping: a dev server's red error line and green ready line are signal, and a
terminal that renders them as plain text is less useful than one that does not.
So:

* **keep** SGR sequences (``ESC [ ... m``) -- colour and text attributes, which
  the frontend converts to styled spans;
* **drop** every other CSI (cursor moves, erase, private modes like ``ESC[?25h``),
  OSC/DCS/PM/APC strings, and single-character escapes -- these encode cursor
  motion that a non-emulating ``<pre>`` cannot honour, so they can only corrupt;
* **drop** C0 controls and DEL, keeping ``\\n`` and ``\\t``; ``\\r\\n`` collapses to
  ``\\n`` and a lone ``\\r`` is dropped, since carriage-return overwrite semantics
  do not exist in a ``<pre>`` either.

This lives in the harness, not ``app/``: ``tests/test_harness_boundary.py``
forbids the harness from importing ``app.*``, so ``app.gateway.utils`` is not
reusable here -- and that helper only strips ``\\n\\r\\x00`` anyway, which is both
too little (no ANSI) and too much (it would eat every newline).
"""

from __future__ import annotations

import re

__all__ = ["sanitize_terminal_text", "strip_all_ansi"]

# One pass, one decision per match. Sequential passes are how the first version
# of this broke: the SGR kept by the CSI pass was then eaten by the catch-all,
# because `\x1b[` matches a bare-escape pattern too. Alternation order matters --
# complete CSI first, then string escapes, then truncated/bare escapes, then the
# remaining control bytes.
_ANSI_OR_CTRL = re.compile(
    # A complete CSI: ESC [ params intermediates final.
    r"(?P<csi>\x1b\[[0-?]*[ -/]*[@-~])"
    # OSC / DCS / SOS / PM / APC: introducer, payload, then ST or BEL.
    r"|(?P<string>\x1b[\]PX^_][^\x1b\x07]*(?:\x07|\x1b\\)?)"
    # A truncated CSI (stream cut mid-sequence), a two-char escape, or a stray ESC.
    r"|(?P<esc>\x1b\[[0-?]*[ -/]*|\x1b[@-Z\\-_]|\x1b)"
    # C0 controls and DEL, minus \n and \t which carry meaning in a <pre>.
    r"|(?P<ctrl>[\x00-\x08\x0b\x0c\x0e-\x1f\x7f])"
)


def _keep_only_colour(match: re.Match[str]) -> str:
    """Keep a complete SGR sequence; drop everything else that matched."""
    csi = match.group("csi")
    return csi if csi is not None and csi.endswith("m") else ""


def sanitize_terminal_text(text: str) -> str:
    """Strip control bytes and non-presentational escapes; keep SGR colour.

    Cheap on the common case: most observations carry none of these, and this
    sits on the write path of every tool call.
    """
    if not text:
        return text
    if "\x1b" not in text and "\r" not in text and not _HAS_CONTROL.search(text):
        return text
    # Normalise line endings first, so a CRLF keeps its break rather than losing
    # it when the \r is dropped.
    text = text.replace("\r\n", "\n").replace("\r", "")
    return _ANSI_OR_CTRL.sub(_keep_only_colour, text)


def strip_all_ansi(text: str) -> str:
    """Same, but drop SGR too -- plain text with no escapes at all.

    For consumers that will never render colour (audit exports, log greps).
    """
    if not text:
        return text
    text = text.replace("\r\n", "\n").replace("\r", "")
    return _ANSI_OR_CTRL.sub("", text)


#: Fast pre-check so the common (clean) case skips the substitution entirely.
_HAS_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
