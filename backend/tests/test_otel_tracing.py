"""Local / self-hosted tracing over OTLP.

The audit asked for "a more sophisticated local trace or OSS self-hosting"
rather than the third-party SaaS default. This pins the properties that
make that safe:

* tracing is OFF unless both the flag and an endpoint are set — an
  exporter with no endpoint drops every span silently, which reads as
  "tracing works" while nothing is collected;
* the OTLP provider is a first-class provider alongside LangSmith and
  Langfuse, so the existing factory path picks it up;
* the handler never raises into the agent loop, and carries the run's
  identity on its spans.
"""

from __future__ import annotations

import pytest

from deerflow.config.tracing_config import (
    get_tracing_config,
    reset_tracing_config,
)


@pytest.fixture(autouse=True)
def _clean_tracing_env(monkeypatch):
    for name in (
        "NOVA_OTEL_TRACING",
        "OTEL_TRACING",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_SERVICE_NAME",
        "LANGSMITH_TRACING",
        "LANGCHAIN_TRACING",
        "LANGFUSE_TRACING",
    ):
        monkeypatch.delenv(name, raising=False)
    reset_tracing_config()
    yield
    reset_tracing_config()


def test_otel_is_off_by_default() -> None:
    from deerflow.tracing.otel import otel_tracing_enabled

    assert otel_tracing_enabled() is False
    assert get_tracing_config().otel.enabled is False
    assert "otel" not in get_tracing_config().enabled_providers


def test_flag_without_endpoint_does_not_enable_tracing(monkeypatch) -> None:
    from deerflow.tracing.otel import otel_tracing_enabled

    # The dangerous half-configured state: a flag with nowhere to send the
    # spans would look like tracing works while collecting nothing.
    # monkeypatch (not os.environ) so the flag cannot leak into the rest of
    # the session: the tracing config is cached process-wide, and a leaked
    # flag made every later test fail validation.
    monkeypatch.setenv("NOVA_OTEL_TRACING", "1")
    reset_tracing_config()
    assert otel_tracing_enabled() is False
    assert get_tracing_config().otel.is_configured is False
    # The provider is not "explicitly enabled" either: it is not usable, so
    # it must not appear among the providers the factory builds.
    assert "otel" not in get_tracing_config().explicitly_enabled_providers


def test_flag_without_endpoint_never_raises_during_validation(monkeypatch) -> None:
    """Validation runs inside the agent's build path.

    An OTLP flag with no collector configured is the expected state of
    "tracing switched on, collector not set up yet" — it must degrade to
    "no spans", never stop the agent from starting.
    """
    from deerflow.config.tracing_config import validate_enabled_tracing_providers

    monkeypatch.setenv("NOVA_OTEL_TRACING", "1")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    reset_tracing_config()
    validate_enabled_tracing_providers()  # must not raise


def test_flag_and_endpoint_enable_tracing(monkeypatch) -> None:
    from deerflow.tracing.otel import otel_tracing_enabled

    monkeypatch.setenv("NOVA_OTEL_TRACING", "1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318/v1/traces")
    reset_tracing_config()
    assert otel_tracing_enabled() is True
    assert get_tracing_config().otel.is_configured is True
    assert "otel" in get_tracing_config().enabled_providers


def test_factory_builds_the_otel_handler(monkeypatch) -> None:
    from deerflow.tracing.factory import build_tracing_callbacks

    monkeypatch.setenv("NOVA_OTEL_TRACING", "1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318/v1/traces")
    reset_tracing_config()
    callbacks = build_tracing_callbacks()
    names = [type(c).__name__ for c in callbacks]
    assert "OTelTracingHandler" in names


def test_factory_returns_nothing_when_tracing_is_off() -> None:
    from deerflow.tracing.factory import build_tracing_callbacks

    assert build_tracing_callbacks() == []


def test_headers_are_parsed_and_malformed_entries_skipped() -> None:
    from deerflow.tracing.otel import _parse_headers

    assert _parse_headers("a=1,b=2") == {"a": "1", "b": "2"}
    assert _parse_headers("a=1,broken,b=2") == {"a": "1", "b": "2"}
    assert _parse_headers("") == {}


def test_handler_never_raises_without_the_otel_sdk(monkeypatch) -> None:
    """A deployment without the OpenTelemetry packages must still work."""
    from deerflow.tracing.otel import OTelTracingHandler

    handler = OTelTracingHandler()
    # No endpoint configured and no SDK: every hook must be a no-op.
    handler.on_chain_start({"name": "x"}, {}, run_id="r1")
    handler.on_chain_end({}, run_id="r1")
    handler.on_llm_start({"name": "m"}, [], run_id="r2")
    handler.on_llm_end(object(), run_id="r2")
    handler.on_tool_start({"name": "t"}, "in", run_id="r3")
    handler.on_tool_end("out", run_id="r3")


def test_handler_stamps_the_run_identity(monkeypatch) -> None:
    """The span must carry the run's ids — the whole point of local traces."""
    from deerflow.runtime.run_context import reset_run_scope, set_run_scope
    from deerflow.tracing.otel import OTelTracingHandler

    captured: dict = {}

    class FakeSpan:
        def __init__(self) -> None:
            self.attributes: dict = {}
            self.ended = False

        def set_attribute(self, key, value):
            self.attributes[key] = value

        def end(self):
            self.ended = True

        def record_exception(self, error):
            captured["exception"] = error

        def set_status(self, status):
            captured["status"] = status

    class FakeTracer:
        def start_span(self, name, context=None, attributes=None):
            span = FakeSpan()
            span.attributes.update(attributes or {})
            captured["name"] = name
            captured["span"] = span
            return span

    handler = OTelTracingHandler(tracer=FakeTracer())
    token = set_run_scope(run_id="run-9", thread_id="thread-9", correlation_id="corr-9")
    try:
        handler.on_llm_start({"name": "chat_model"}, [], run_id="r1")
    finally:
        reset_run_scope(token)

    assert captured["name"] == "llm.chat_model"
    assert captured["span"].attributes["run_id"] == "run-9"
    assert captured["span"].attributes["thread_id"] == "thread-9"
    assert captured["span"].attributes["correlation_id"] == "corr-9"


def test_llm_end_records_token_usage(monkeypatch) -> None:
    from deerflow.tracing.otel import OTelTracingHandler

    captured: dict = {}

    class FakeSpan:
        def __init__(self) -> None:
            self.attributes: dict = {}

        def set_attribute(self, key, value):
            self.attributes[key] = value

        def end(self):
            captured["ended"] = True

        def record_exception(self, error):
            pass

        def set_status(self, status):
            pass

    class FakeTracer:
        def start_span(self, name, context=None, attributes=None):
            span = FakeSpan()
            span.attributes.update(attributes or {})
            captured["span"] = span
            return span

    class FakeMessage:
        usage_metadata = {"input_tokens": 11, "output_tokens": 3, "total_tokens": 14}
        response_metadata = {"model_name": "MiniMax-M3"}

    class FakeGeneration:
        message = FakeMessage()

    class FakeResponse:
        generations = [[FakeGeneration()]]

    handler = OTelTracingHandler(tracer=FakeTracer())
    handler.on_llm_start({"name": "chat_model"}, [], run_id="r1")
    handler.on_llm_end(FakeResponse(), run_id="r1")

    attrs = captured["span"].attributes
    assert attrs["gen_ai.usage.input_tokens"] == 11
    assert attrs["gen_ai.usage.output_tokens"] == 3
    assert attrs["gen_ai.usage.total_tokens"] == 14
    assert attrs["gen_ai.response.model"] == "MiniMax-M3"
    assert captured.get("ended") is True


def test_langsmith_stays_opt_in_and_off(monkeypatch) -> None:
    """The third-party SaaS must not become the default."""
    config = get_tracing_config()
    assert config.langsmith.enabled is False
    assert "langsmith" not in config.enabled_providers
