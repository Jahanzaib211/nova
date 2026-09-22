"""Tests for SearXNG community tools.

Covers:
- SearxngClient.search: success / empty / HTTP error / connection error / category
  passthrough. Uses httpx.AsyncClient mocked via unittest.mock.AsyncMock.
- web_search_tool: success / error / max_results passthrough. Mocks the
  _get_searxng_client helper so the tool doesn't reach for a real network.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from deerflow.community.searxng import tools
from deerflow.community.searxng.searxng_client import SearxngClient


def _make_httpx_mock(
    *,
    status_code: int = 200,
    body: dict | None = None,
    raise_for_status_exc: Exception | None = None,
) -> MagicMock:
    """Build a mock that satisfies the `async with httpx.AsyncClient() as client` pattern."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json = MagicMock(return_value=body or {})
    if raise_for_status_exc is None:
        mock_response.raise_for_status = MagicMock(return_value=None)
    else:
        mock_response.raise_for_status = MagicMock(side_effect=raise_for_status_exc)

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


@pytest.mark.asyncio
class TestSearxngClient:
    """Tests for the SearxngClient class."""

    async def test_search_success(self):
        """Search returns normalized results."""
        results_data = {
            "results": [
                {"title": "Page 1", "url": "https://example.com/1", "content": "Snippet 1"},
                {"title": "Page 2", "url": "https://example.com/2", "content": "Snippet 2"},
            ]
        }

        mock_client = _make_httpx_mock(status_code=200, body=results_data)
        with patch("deerflow.community.searxng.searxng_client.httpx.AsyncClient", return_value=mock_client):
            client = SearxngClient(base_url="http://searxng:8080")
            result = await client.search("test query", max_results=5)

            assert len(result) == 2
            assert result[0]["title"] == "Page 1"
            assert result[1]["url"] == "https://example.com/2"

    async def test_search_empty_results(self):
        """Search returns empty list when no results."""
        mock_client = _make_httpx_mock(status_code=200, body={"results": []})
        with patch("deerflow.community.searxng.searxng_client.httpx.AsyncClient", return_value=mock_client):
            client = SearxngClient(base_url="http://searxng:8080")
            result = await client.search("empty query")
            assert result == []

    async def test_search_http_error(self):
        """Search raises SearchPermanentError on 4xx (not retried)."""
        # The client converts 4xx into SearchPermanentError before
        # raise_for_status() is reached (see searxng_client.py status
        # handling); we assert that wrapper instead of raw HTTPStatusError.
        from deerflow.community.searxng.search_errors import SearchPermanentError

        mock_client = _make_httpx_mock(status_code=403)
        with patch("deerflow.community.searxng.searxng_client.httpx.AsyncClient", return_value=mock_client):
            client = SearxngClient(base_url="http://searxng:8080")
            with pytest.raises(SearchPermanentError):
                await client.search("blocked query")

    async def test_search_request_error(self):
        """Search raises on connection-level error."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("deerflow.community.searxng.searxng_client.httpx.AsyncClient", return_value=mock_client):
            client = SearxngClient(base_url="http://searxng:8080")
            # ConnectError is retried 3x then surfaces as SearchConnectionError.
            from deerflow.community.searxng.search_errors import SearchConnectionError

            with pytest.raises(SearchConnectionError):
                await client.search("unreachable query")

    async def test_search_with_categories(self):
        """Search passes categories parameter to SearXNG."""
        mock_client = _make_httpx_mock(status_code=200, body={"results": []})
        with patch("deerflow.community.searxng.searxng_client.httpx.AsyncClient", return_value=mock_client):
            client = SearxngClient(base_url="http://searxng:8080")
            await client.search("test", categories=["news", "science"])

            call_kwargs = mock_client.get.call_args.kwargs
            assert call_kwargs["params"]["categories"] == "news,science"


@pytest.mark.asyncio
class TestSearxngTools:
    """Tests for the SearXNG tool functions."""

    async def test_web_search_tool_success(self):
        """web_search_tool returns JSON results from the SearXNG client."""
        mock_client = MagicMock()
        mock_client.search = AsyncMock(
            return_value=[
                {"title": "Result 1", "url": "https://example.com/1", "content": "Desc 1"},
            ]
        )

        with patch("deerflow.community.searxng.tools._get_searxng_client", return_value=mock_client), patch("deerflow.community.searxng.tools._get_tool_config", return_value=None):
            result = await tools.web_search_tool.ainvoke({"query": "test query"})

        data = json.loads(result)
        assert data["count"] == 1
        assert data["results"][0]["title"] == "Result 1"
        assert data["source"] == "searxng"

    async def test_web_search_tool_error(self):
        """web_search_tool returns error JSON when both SearXNG and DDG fail."""
        # Mock both the SearXNG client and the DDG fallback module so the
        # tool's except-branch runs to completion.
        mock_searxng = MagicMock()
        mock_searxng.search = AsyncMock(side_effect=Exception("API error"))

        with (
            patch("deerflow.community.searxng.tools._get_searxng_client", return_value=mock_searxng),
            patch("deerflow.community.searxng.tools._get_tool_config", return_value=None),
            patch("deerflow.community.ddg_search.tools.web_search_tool") as mock_ddg,
        ):
            # Make the DDG tool's ainvoke raise too.
            mock_ddg.ainvoke = AsyncMock(side_effect=Exception("DDG also failed"))
            result = await tools.web_search_tool.ainvoke({"query": "test query"})

        data = json.loads(result)
        assert data["query"] == "test query"
        assert data["results"] == []
        # source is 'none' when both backends failed; error key present.
        assert data["source"] == "none"
        assert "error" in data

    async def test_web_search_tool_with_max_results(self):
        """web_search_tool coerces max_results from string config."""
        mock_client = MagicMock()
        mock_client.search = AsyncMock(return_value=[{"title": f"R{i}", "url": f"https://example.com/{i}", "content": f"D{i}"} for i in range(10)])

        with patch("deerflow.community.searxng.tools._get_searxng_client", return_value=mock_client), patch("deerflow.community.searxng.tools._get_tool_config", return_value={"max_results": "3"}):
            await tools.web_search_tool.ainvoke({"query": "test query"})

        mock_client.search.assert_called_once()
        call_kwargs = mock_client.search.call_args.kwargs
        assert call_kwargs["max_results"] == 3


@pytest.mark.asyncio
class TestProbeHealthMatchesHealth:
    """``probe_health()`` and ``health()`` must agree about the same instance.

    ``probe_health()`` (capabilities bar) tried ``/healthz`` only, while
    ``health()`` (Privacy panel) also falls back to ``/``. A SearXNG behind a
    proxy that forwards ``/search`` but not ``/healthz`` therefore read healthy
    in one surface and unhealthy in the other.
    """

    async def _client(self):
        from deerflow.community.searxng.searxng_client import SearxngClient

        return SearxngClient(base_url="http://searxng:8080")

    async def test_healthz_ok_is_healthy(self):
        client = await self._client()
        with patch("deerflow.community.searxng.searxng_client.httpx.AsyncClient", return_value=_make_httpx_mock(status_code=200)):
            assert await client.probe_health() is True

    async def test_falls_back_to_root_when_healthz_404s(self):
        """The behaviour change: 404 on /healthz must not end the probe."""
        client = await self._client()
        responses = [MagicMock(status_code=404), MagicMock(status_code=200)]

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=responses)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("deerflow.community.searxng.searxng_client.httpx.AsyncClient", return_value=mock_client):
            assert await client.probe_health() is True
        assert mock_client.get.await_count == 2, "the root fallback should have been tried"

    async def test_both_paths_failing_is_unhealthy(self):
        client = await self._client()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("deerflow.community.searxng.searxng_client.httpx.AsyncClient", return_value=mock_client):
            assert await client.probe_health() is False
        assert mock_client.get.await_count == 2

    async def test_server_error_on_both_paths_is_unhealthy(self):
        client = await self._client()
        with patch("deerflow.community.searxng.searxng_client.httpx.AsyncClient", return_value=_make_httpx_mock(status_code=502)):
            assert await client.probe_health() is False
