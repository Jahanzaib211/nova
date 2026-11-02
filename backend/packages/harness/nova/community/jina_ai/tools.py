import asyncio
import logging
from urllib.parse import urlsplit

import httpx
from langchain.tools import tool

from deerflow.community.jina_ai.jina_client import JinaClient
from deerflow.config import get_app_config
from deerflow.utils.readability import ReadabilityExtractor

logger = logging.getLogger(__name__)

readability_extractor = ReadabilityExtractor()

# A browser-ish UA. Plenty of sites 403 the default httpx UA outright, which
# would make the fallback look broken when the page is in fact reachable.
_DIRECT_FETCH_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


async def _direct_fetch(url: str, timeout: int, proxy: str | None, trust_env: bool) -> str:
    """Fetch ``url`` directly, bypassing Jina. Returns HTML or an ``Error:`` string.

    This is the keyless fallback. r.jina.ai used to serve anonymous requests and
    now answers 401 without an API key, which left ``web_fetch`` dead on any
    deployment that had never set ``JINA_API_KEY`` — the tool simply returned the
    upstream error string to the model.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return f"Error: unsupported URL scheme {parts.scheme!r}; only http and https are fetchable."
    client_kwargs: dict[str, object] = {"trust_env": trust_env, "follow_redirects": True}
    if proxy:
        client_kwargs["proxy"] = proxy
    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            response = await client.get(url, headers={"User-Agent": _DIRECT_FETCH_UA}, timeout=timeout)
        if response.status_code != 200:
            return f"Error: direct fetch returned status {response.status_code}"
        if not response.text or not response.text.strip():
            return "Error: direct fetch returned an empty response"
        return response.text
    except Exception as e:
        return f"Error: direct fetch failed: {type(e).__name__}: {e}"


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_timeout(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _coerce_proxy(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    proxy = value.strip()
    return proxy or None


@tool("web_fetch", parse_docstring=True)
async def web_fetch_tool(url: str) -> str:
    """Fetch the contents of a web page at a given URL.
    Only fetch EXACT URLs that have been provided directly by the user or have been returned in results from the web_search and web_fetch tools.
    This tool can NOT access content that requires authentication, such as private Google Docs or pages behind login walls.
    Do NOT add www. to URLs that do NOT have them.
    URLs must include the schema: https://example.com is a valid URL while example.com is an invalid URL.

    Args:
        url: The URL to fetch the contents of.
    """
    jina_client = JinaClient()
    timeout = 10
    proxy = None
    trust_env = True
    config = get_app_config().get_tool_config("web_fetch")
    if config is not None:
        timeout = _coerce_timeout(config.model_extra.get("timeout"), timeout)
        proxy = _coerce_proxy(config.model_extra.get("proxy"))
        trust_env = _coerce_bool(config.model_extra.get("trust_env"), trust_env)
    html_content = await jina_client.crawl(url, return_format="html", timeout=timeout, proxy=proxy, trust_env=trust_env)
    if isinstance(html_content, str) and html_content.startswith("Error:"):
        jina_error = html_content
        logger.info("web_fetch: Jina failed (%s); falling back to a direct fetch", jina_error)
        html_content = await _direct_fetch(url, timeout=timeout, proxy=proxy, trust_env=trust_env)
        if isinstance(html_content, str) and html_content.startswith("Error:"):
            return f"{jina_error}\n{html_content}\nBoth the Jina reader and a direct fetch failed. Set JINA_API_KEY to use the Jina reader (r.jina.ai now rejects anonymous requests with 401)."
    article = await asyncio.to_thread(readability_extractor.extract_article, html_content)
    return article.to_markdown()[:4096]
