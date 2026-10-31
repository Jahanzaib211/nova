"""``deps.is_admin`` and ``deps.authenticated_user_id``: one copy each.

The admin predicate was re-implemented in ``routers/capabilities_ops.py``
and ``routers/models.py``; ``_user_id(request)`` was copied verbatim into
``routers/jobs.py`` and ``routers/email_marketing.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.gateway import deps


def test_is_admin():
    assert deps.is_admin(SimpleNamespace(system_role="admin")) is True
    assert deps.is_admin(SimpleNamespace(system_role="user")) is False
    assert deps.is_admin(object()) is False


def test_authenticated_user_id():
    req = SimpleNamespace(state=SimpleNamespace(auth=SimpleNamespace(user=SimpleNamespace(id=42))))
    assert deps.authenticated_user_id(req) == "42"
    for state in (SimpleNamespace(auth=SimpleNamespace(user=None)), SimpleNamespace()):
        with pytest.raises(HTTPException) as exc:
            deps.authenticated_user_id(SimpleNamespace(state=state))
        assert exc.value.status_code == 401


def test_routers_use_the_shared_helpers():
    import inspect

    from app.gateway.routers import capabilities_ops, email_marketing, jobs, models

    assert jobs._user_id is deps.authenticated_user_id
    assert email_marketing._user_id is deps.authenticated_user_id
    for module in (capabilities_ops, models):
        assert 'system_role", None) == "admin"' not in inspect.getsource(module)
