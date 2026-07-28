"""Regression tests for SIGTERM/SIGINT handler chaining in install_shutdown_hooks.

Root cause of the wedged dev reloader (2026-07-12): install_shutdown_hooks()
replaced uvicorn's signal handler via signal.signal() WITHOUT chaining to the
previous handler. Uvicorn's handle_exit (which sets Server.should_exit) never
ran, so on reload the old worker kept serving forever and code changes
silently never landed. The fix saves the previous handler and invokes it
after the sandbox cleanup runs.
"""

import signal

import pytest

from deerflow.sandbox import shutdown as shutdown_mod


@pytest.fixture()
def fresh_hooks(monkeypatch):
    """Allow install_shutdown_hooks() to install again in this process."""
    monkeypatch.setattr(shutdown_mod, "_installed", False)
    shutdown_mod.reset_for_testing()
    original_term = signal.getsignal(signal.SIGTERM)
    original_int = signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGTERM, original_term)
    signal.signal(signal.SIGINT, original_int)


def test_sigterm_chains_to_previous_handler(fresh_hooks, monkeypatch):
    """Our handler must invoke whatever handler was installed before it
    (uvicorn's handle_exit in production)."""
    chained: list[tuple[int, object]] = []

    def uvicorn_like_handler(signum, frame):
        chained.append((signum, frame))

    signal.signal(signal.SIGTERM, uvicorn_like_handler)

    ran_shutdown: list[bool] = []
    monkeypatch.setattr(shutdown_mod, "_safe_shutdown", lambda: ran_shutdown.append(True))

    assert shutdown_mod.install_shutdown_hooks() is True

    installed = signal.getsignal(signal.SIGTERM)
    assert callable(installed)
    assert installed is not uvicorn_like_handler

    installed(signal.SIGTERM, None)

    assert ran_shutdown == [True], "sandbox cleanup must still run"
    assert chained == [(signal.SIGTERM, None)], "previous (uvicorn) handler must be invoked after cleanup — otherwise the server never sees the signal and reloads wedge"


def test_sigint_chains_to_previous_handler(fresh_hooks, monkeypatch):
    chained: list[int] = []
    signal.signal(signal.SIGINT, lambda s, f: chained.append(s))
    monkeypatch.setattr(shutdown_mod, "_safe_shutdown", lambda: None)

    shutdown_mod.install_shutdown_hooks()
    handler = signal.getsignal(signal.SIGINT)
    assert callable(handler)
    handler(signal.SIGINT, None)

    assert chained == [signal.SIGINT]


def test_second_install_is_noop(fresh_hooks, monkeypatch):
    monkeypatch.setattr(shutdown_mod, "_safe_shutdown", lambda: None)
    assert shutdown_mod.install_shutdown_hooks() is True
    assert shutdown_mod.install_shutdown_hooks() is False
