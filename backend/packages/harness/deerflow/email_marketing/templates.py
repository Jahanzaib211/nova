"""Campaign templates: sandboxed Jinja2 with an allowlisted context.

Templates are user-authored HTML. They render in Jinja2's
``SandboxedEnvironment`` with autoescape on, no loaders (so no ``include``/
``import``), strict undefined (a typo renders as an error, not a blank), and
a context limited to the contact's fields plus the links Nova generates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any

from deerflow.email_marketing.tracking import TrackingLinks


class TemplateError(ValueError):
    """Syntax error, sandbox violation or unknown variable in a template."""


_SKIP_SCHEMES = ("mailto:", "tel:", "sms:", "#", "{{", "{%", "data:", "javascript:")
_HREF_RE = re.compile(r"""(<a\b[^>]*?\bhref\s*=\s*)(["'])(.*?)\2""", re.IGNORECASE | re.DOTALL)

_FOOTER = '<p style="font-size:12px;color:#888;margin-top:24px">You are receiving this because you subscribed. <a href="{{ unsubscribe_url }}">Unsubscribe</a></p>'


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    html: str
    text: str


def _env():
    from jinja2 import StrictUndefined
    from jinja2.sandbox import ImmutableSandboxedEnvironment

    return ImmutableSandboxedEnvironment(autoescape=True, undefined=StrictUndefined, loader=None, enable_async=False)


def _render(env: Any, source: str, context: dict[str, Any]) -> str:
    from jinja2 import TemplateError as JinjaTemplateError
    from jinja2.exceptions import SecurityError, UndefinedError
    from markupsafe import Markup

    try:
        return env.from_string(source).render(**{k: (Markup(v) if k in _SAFE_KEYS else v) for k, v in context.items()})
    except (SecurityError, UndefinedError, JinjaTemplateError) as exc:
        raise TemplateError(str(exc)) from exc
    except Exception as exc:  # e.g. TypeError "no loader" from {% include %}
        raise TemplateError(f"template cannot be rendered: {exc}") from exc


_SAFE_KEYS = frozenset({"tracking_pixel"})


def validate_template(source: str) -> str | None:
    """Syntax check without rendering; returns the error text, or None."""
    from jinja2 import TemplateError as JinjaTemplateError

    try:
        _env().parse(source)
    except JinjaTemplateError as exc:
        return str(exc)
    return None


def rewrite_links(html: str, links: TrackingLinks) -> str:
    """Route every http(s) anchor through the click tracker; leave the
    unsubscribe link, anchors and mailto alone."""
    unsub_prefix = f"{links.base_url.rstrip('/')}/api/em/u/"

    def sub(match: re.Match[str]) -> str:
        prefix, quote, target = match.group(1), match.group(2), match.group(3)
        t = target.strip()
        if not t or t.lower().startswith(_SKIP_SCHEMES) or t.startswith(unsub_prefix) or "/api/em/t/" in t:
            return match.group(0)
        if not t.lower().startswith(("http://", "https://")):
            return match.group(0)
        return f"{prefix}{quote}{links.click_url(unescape(t))}{quote}"

    return _HREF_RE.sub(sub, html)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._href: str | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("style", "script", "head"):
            self._skip += 1
        elif tag in ("p", "div", "br", "h1", "h2", "h3", "h4", "tr", "table"):
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "a":
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "script", "head"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._href and not self._href.startswith(("#", "mailto:")):
            self.parts.append(f" ({self._href})")
            self._href = None
        elif tag in ("p", "div", "h1", "h2", "h3", "h4", "ul", "ol"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def render_template(
    *,
    subject: str,
    html: str,
    text: str | None,
    contact: dict[str, Any],
    links: TrackingLinks,
    view_in_browser_url: str | None = None,
) -> RenderedEmail:
    """Render one message for one contact. Raises TemplateError."""
    env = _env()
    pixel = f'<img src="{links.open_pixel_url()}" width="1" height="1" alt="" style="display:none">'
    context: dict[str, Any] = {
        "contact": {
            "email": contact.get("email", ""),
            "first_name": contact.get("first_name") or "",
            "last_name": contact.get("last_name") or "",
            "attributes": dict(contact.get("attributes") or {}),
        },
        "unsubscribe_url": links.unsubscribe_url(),
        "view_in_browser_url": view_in_browser_url or "",
        "tracking_pixel": pixel,
    }
    rendered_subject = _render(env, subject, context).strip()
    body = _render(env, html, context)
    if "unsubscribe_url" not in html:
        body += _render(env, _FOOTER, context)
    if "{{ tracking_pixel }}" not in html and "tracking_pixel" not in html:
        body += pixel
    body = rewrite_links(body, links)
    rendered_text = _render(env, text, context) if text else html_to_text(body)
    return RenderedEmail(subject=rendered_subject, html=body, text=rendered_text)
