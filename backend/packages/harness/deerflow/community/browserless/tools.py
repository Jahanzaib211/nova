import asyncio
import logging
import time

from langchain.tools import tool

from deerflow.config import get_app_config
from deerflow.utils.readability import ReadabilityExtractor

from .browserless_client import BrowserlessClient

logger = logging.getLogger(__name__)

# readability_extractor runs CPU-bound parsing; always call via asyncio.to_thread
_readability_extractor = ReadabilityExtractor()


def _get_tool_config(tool_name: str) -> dict | None:
    """Get tool config extras safely, returning None if not configured."""
    config = get_app_config().get_tool_config(tool_name)
    if config is None:
        return None
    extras = config.model_extra
    return extras if extras is not None else {}


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


@tool("web_fetch", parse_docstring=True)
async def web_fetch_tool(url: str) -> str:
    """Fetch the contents of a web page at a given URL using Browserless (headless Chrome).
    Only fetch EXACT URLs that have been provided directly by the user or have been returned in results from the web_search and web_fetch tools.
    This tool can NOT access content that requires authentication, such as private Google Docs or pages behind login walls.
    Do NOT add www. to URLs that do NOT have them.
    URLs must include the schema: https://example.com is a valid URL while example.com is an invalid URL.

    Args:
        url: The URL to fetch the contents of.
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
        html = await client.fetch_html(
            url=url,
            wait_for_event=wait_for_event,
            wait_for_timeout_ms=wait_for_timeout_ms,
            wait_for_selector=wait_for_selector,
            wait_for_selector_timeout_ms=wait_for_selector_timeout_ms,
            reject_resource_types=reject_resource_types,
            reject_request_pattern=reject_request_pattern,
        )

        elapsed_ms = (time.monotonic() - start) * 1000

        if html.startswith("Error:"):
            _record_fetch(url, success=False, duration_ms=elapsed_ms, error=html[:200])
            return html

        article = await asyncio.to_thread(_readability_extractor.extract_article, html)
        _record_fetch(url, success=True, duration_ms=elapsed_ms)
        return article.to_markdown()[:4096]

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
        from deerflow.community.searxng.audit import get_audit_trail

        get_audit_trail().fetch(
            url=url,
            source="browserless",
            success=success,
            duration_ms=duration_ms,
            error=error,
        )
    except Exception:  # noqa: BLE001 - never let auditing break the tool
        logger.debug("fetch audit failed for %s", url, exc_info=True)
