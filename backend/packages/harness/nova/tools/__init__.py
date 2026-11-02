__all__ = ["get_available_tools", "skill_manage_tool"]


def __getattr__(name: str):
    # Both exports are lazy. `get_available_tools` used to be imported eagerly,
    # so importing any submodule (e.g. `deerflow.tools.types`, which
    # `deerflow.sandbox.tools` needs) pulled in `.tools` -> `workspace_tools` ->
    # `deerflow.sandbox.tools` while that module was still half-built, and a
    # cold `import deerflow.sandbox.tools` raised. Pinned by
    # tests/test_import_hygiene.py.
    if name == "get_available_tools":
        from .tools import get_available_tools

        return get_available_tools
    if name == "skill_manage_tool":
        from .skill_manage_tool import skill_manage_tool

        return skill_manage_tool
    raise AttributeError(name)
