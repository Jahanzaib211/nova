"""Shared lookup of a tool's ``config.yaml`` extras.

The SearXNG, Browserless and both Crawl4AI tool modules carried identical
copies of this function.
"""

from __future__ import annotations

from typing import Any

from deerflow.config import get_app_config


def tool_config_extras(tool_name: str) -> dict[str, Any] | None:
    """The extra keys (``base_url``, ``token``, …) of tool ``tool_name``'s
    config entry; ``{}`` when it has none, ``None`` when it is not configured."""
    config = get_app_config().get_tool_config(tool_name)
    if config is None:
        return None
    extras = config.model_extra
    return extras if extras is not None else {}
