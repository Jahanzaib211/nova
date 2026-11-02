def __getattr__(name: str):
    if name == "web_search_tool":
        from .tools import web_search_tool

        return web_search_tool
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["web_search_tool"]
