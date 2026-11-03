import asyncio
import logging
import time

from langchain.tools import tool

from nova.browser.escalation import fetch_with_ladder
from nova.browser.profiles import cookies_for_fetch
from nova.community._tool_config import tool_config_extras as _get_tool_config
from nova.config.browser_config import get_browser_config
from nova.tools.types import Runtime
from nova.utils.readability import ReadabilityExtractor

from .browserless_client import BrowserlessClient

logger = logging.getLogger(__name__)

# readability_extractor runs CPU-bound parsing; always call via asyncio.to_thread
_readability_extractor = ReadabilityExtractor()


def _get_browserless_client() -> BrowserlessClient:
    cfg = _get_tool_config("web_fetch")
    base_url = "http://localhost:3032"
    token = ""
    timeout_s = 30.0
    if cfg is not None:
        base_url = cfg.get("base_url", base_url)
        token = cfg.get("token", token)
        raw = cfg.get("timeout_s", timeout_s)
        timeout_s = float(raw) if not isinstance(raw, float) else raw
    return BrowserlessClient(base_url=base_url, token=token, timeout_s=timeout_s)


def _fetch_user_id(runtime) -> str | None:
    """The user whose saved profiles may be used. None outside an authenticated run."""
    from nova.runtime.user_context import DEFAULT_USER_ID, resolve_runtime_user_id

    try:
        uid = resolve_runtime_user_id(runtime)
    except Exception:  # noqa: BLE001
        return None
    # The no-auth "default" bucket is a real single-user install's bucket, so it
    # is honoured; it is only ever reached when no authenticated user exists.
    return uid or DEFAULT_USER_ID


@tool("web_fetch", parse_docstring=True)
async def web_fetch_tool(url: str, runtime: Runtime = None, use_profile: bool = True) -> str:
    """Fetch the contents of a web page at a given URL using Browserless (headless Chrome).
    Only fetch EXACT URLs that have been provided directly by the user or have been returned in results from the web_search and web_fetch tools.
    Pages behind a login work when the user saved a browser profile for that site (browser_profile_save); its cookies are sent automatically.
    If a site answers with a bot wall, the fetch escalates (stealth, then the sandbox browser); a result starting with NEEDS_HUMAN means a captcha needs the user.
    Do NOT add www. to URLs that do NOT have them.
    URLs must include the schema: https://example.com is a valid URL while example.com is an invalid URL.

    Args:
        url: The URL to fetch the contents of.
        use_profile: Send the user's saved login cookies for this site (default true). Set false to fetch anonymously.
    """
    try:
        cfg = _get_tool_config("web_fetch")

        wait_for_event = ""
        wait_for_timeout_ms = 0
        wait_for_selector = ""
        wait_for_selector_timeout_ms = 5000
        reject_resource_types: list[str] | None = None
        reject_request_pattern: list[str] | None = None

        if cfg is not None:
            wait_for_event = cfg.get("wait_for_event", wait_for_event)
            raw_wait = cfg.get("wait_for_timeout_ms", wait_for_timeout_ms)
            wait_for_timeout_ms = int(raw_wait) if not isinstance(raw_wait, int) else raw_wait
            wait_for_selector = cfg.get("wait_for_selector", wait_for_selector)

        start = time.monotonic()
        client = _get_browserless_client()
        base_kwargs = {
            "wait_for_event": wait_for_event,
            "wait_for_timeout_ms": wait_for_timeout_ms,
            "wait_for_selector": wait_for_selector,
            "wait_for_selector_timeout_ms": wait_for_selector_timeout_ms,
            "reject_resource_types": reject_resource_types,
            "reject_request_pattern": reject_request_pattern,
        }
        browser_cfg = get_browser_config()
        user_id = _fetch_user_id(runtime)
        cookies = await asyncio.to_thread(cookies_for_fetch, url, user_id, use_profile=use_profile)
        from nova.tools.builtins.browser_layer_tools import sandbox_fetcher

        ladder = await fetch_with_ladder(url, client, base_kwargs, browser_cfg, cookies=cookies, proxy_server=browser_cfg.proxy.server, sandbox_fetch=sandbox_fetcher(runtime))

        elapsed_ms = (time.monotonic() - start) * 1000

        if ladder.error and not ladder.html:
            _record_fetch(url, success=False, duration_ms=elapsed_ms, error=ladder.error[:200])
            return ladder.error

        if ladder.block is not None and ladder.block.needs_human:
            from nova.browser.session import needs_human_message

            _record_fetch(url, success=False, duration_ms=elapsed_ms, error=f"blocked: {ladder.block.kind}")
            return needs_human_message(ladder.block, url) + f"\n({ladder.summary()})"

        article = await asyncio.to_thread(_readability_extractor.extract_article, ladder.html)
        _record_fetch(url, success=True, duration_ms=elapsed_ms)
        notes = []
        if len(ladder.steps) > 1 or ladder.engine != "browserless":
            notes.append(ladder.summary())
        if cookies:
            notes.append("sent the saved browser profile's cookies for this site")
        if ladder.block is not None:
            notes.append(f"page may still be a {ladder.block.kind} challenge")
        suffix = f"\n\n_({'; '.join(notes)})_" if notes else ""
        return article.to_markdown()[: 4096 - len(suffix)] + suffix

    except Exception as e:
        logger.error(f"Error in web_fetch_tool: {e}")
        _record_fetch(url, success=False, duration_ms=0.0, error=str(e)[:200])
        return f"Error: {str(e)}"


def _record_fetch(url: str, *, success: bool, duration_ms: float, error: str | None = None) -> None:
    """Record the fetch in the shared audit trail. Never fatal.

    The trail has carried a ``fetch()`` method with no caller, which is why the
    Privacy panel's crawler figures were always zero -- the facility existed and
    nothing wrote to it. Bookkeeping must never break a fetch that succeeded, so
    every failure here is swallowed.
    """
    try:
        from nova.community.searxng.audit import get_audit_trail

        get_audit_trail().fetch(
            url=url,
            source="browserless",
            success=success,
            duration_ms=duration_ms,
            error=error,
        )
    except Exception:  # noqa: BLE001 - never let auditing break the tool
        logger.debug("fetch audit failed for %s", url, exc_info=True)
