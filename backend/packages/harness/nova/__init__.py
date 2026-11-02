"""``nova`` — the Nova agent harness.

This is the real package. Nova was forked from DeerFlow, and the harness used
to live under the ``deerflow`` import name; that name is kept as a deprecated
compatibility alias (see ``deerflow/__init__.py``) so existing ``use:`` paths
in a live ``config.yaml``, pickled references and out-of-tree scripts keep
resolving. ``import deerflow.X`` returns the very same module object as
``import nova.X`` -- module-level singletons never exist twice.
"""
