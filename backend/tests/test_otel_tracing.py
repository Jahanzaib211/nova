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

from nova.config.tracing_config import (
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
    from nova.tracing.otel import otel_tracing_enabled

    assert otel_tracing_enabled() is False
    assert get_tracing_config().otel.enabled is False
    assert "otel" not in get_tracing_config().enabled_providers


def test_flag_without_endpoint_does_not_enable_tracing(monkeypatch) -> None:
    from nova.tracing.otel import otel_tracing_enabled

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
    from nova.config.tracing_config import validate_enabled_tracing_providers

    monkeypatch.setenv("NOVA_OTEL_TRACING", "1")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    reset_tracing_config()
    validate_enabled_tracing_providers()  # must not raise


def test_flag_and_endpoint_enable_tracing(monkeypatch) -> None:
    from nova.tracing.otel import otel_tracing_enabled

    monkeypatch.setenv("NOVA_OTEL_TRACING", "1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318/v1/traces")
    reset_tracing_config()
    assert otel_tracing_enabled() is True
    assert get_tracing_config().otel.is_configured is True
    assert "otel" in get_tracing_config().enabled_providers


def test_factory_builds_the_otel_handler(monkeypatch) -> None:
    from nova.tracing.factory import build_tracing_callbacks

    monkeypatch.setenv("NOVA_OTEL_TRACING", "1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318/v1/traces")
    reset_tracing_config()
    callbacks = build_tracing_callbacks()
    names = [type(c).__name__ for c in callbacks]
    assert "OTelTracingHandler" in names


def test_factory_returns_nothing_when_tracing_is_off() -> None:
    from nova.tracing.factory import build_tracing_callbacks

    assert build_tracing_callbacks() == []


def test_headers_are_parsed_and_malformed_entries_skipped() -> None:
    from nova.tracing.otel import _parse_headers

    assert _parse_headers("a=1,b=2") == {"a": "1", "b": "2"}
    assert _parse_headers("a=1,broken,b=2") == {"a": "1", "b": "2"}
    assert _parse_headers("") == {}


def test_handler_never_raises_without_the_otel_sdk(monkeypatch) -> None:
    """A deployment without the OpenTelemetry packages must still work."""
    from nova.tracing.otel import OTelTracingHandler

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
    from nova.runtime.run_context import reset_run_scope, set_run_scope
    from nova.tracing.otel import OTelTracingHandler

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
    from nova.tracing.otel import OTelTracingHandler

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


# --- the real SDK ----------------------------------------------------------
#
# Every test above uses a fake tracer, which is how a provider that was never
# installed went unnoticed: ``hasattr(trace, "_TRACER_PROVIDER")`` is always
# true (the API defines it as ``None``), so the exporter was never built and
# every span was a NonRecordingSpan. These drive the real SDK.


def test_real_sdk_records_spans_with_parents_and_error_status() -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import StatusCode

    from nova.tracing.otel import OTelTracingHandler

    exporter = InMemorySpanExporter()
    handler = OTelTracingHandler(service_name="nova-test", span_exporter=exporter)

    handler.on_chain_start({"name": "agent"}, {}, run_id="chain-1")
    handler.on_llm_start({"name": "chat_model"}, [], run_id="llm-1", parent_run_id="chain-1")
    handler.on_llm_end(object(), run_id="llm-1")
    handler.on_tool_start({"name": "bash"}, "ls", run_id="tool-1", parent_run_id="chain-1")
    handler.on_tool_error(RuntimeError("command failed"), run_id="tool-1")
    handler.on_chain_end({}, run_id="chain-1")

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert set(spans) == {"chain.agent", "llm.chat_model", "tool.bash"}
    chain, llm, tool = spans["chain.agent"], spans["llm.chat_model"], spans["tool.bash"]

    assert chain.parent is None
    assert llm.parent is not None and llm.parent.span_id == chain.context.span_id
    assert tool.parent is not None and tool.parent.span_id == chain.context.span_id
    assert {llm.context.trace_id, tool.context.trace_id} == {chain.context.trace_id}

    # The error is recorded BEFORE the span ends -- it used to be recorded
    # on an already-popped span, i.e. never.
    assert tool.status.status_code == StatusCode.ERROR
    assert "command failed" in (tool.status.description or "")
    assert any(e.name == "exception" for e in tool.events)
    assert tool.attributes["error.type"] == "RuntimeError"
    assert chain.status.status_code != StatusCode.ERROR
    assert llm.status.status_code != StatusCode.ERROR
    assert chain.resource.attributes["service.name"] == "nova-test"


def test_chain_and_llm_errors_are_recorded_too() -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import StatusCode

    from nova.tracing.otel import OTelTracingHandler

    exporter = InMemorySpanExporter()
    handler = OTelTracingHandler(span_exporter=exporter)
    handler.on_chain_start({"name": "agent"}, {}, run_id="c")
    handler.on_llm_start({"name": "m"}, [], run_id="l", parent_run_id="c")
    handler.on_llm_error(ValueError("rate limited"), run_id="l")
    handler.on_chain_error(ValueError("rate limited"), run_id="c")

    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    assert all(s.status.status_code == StatusCode.ERROR for s in spans)
    assert all(any(e.name == "exception" for e in s.events) for s in spans)


def test_enabled_handler_gets_a_recording_tracer_from_the_owned_provider(monkeypatch) -> None:
    """With the flag and an endpoint set, spans must be recording -- the
    regression was a tracer from the API's default (no-op) provider."""
    import atexit

    from nova.tracing import otel

    monkeypatch.setenv("NOVA_OTEL_TRACING", "1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9/v1/traces")
    monkeypatch.setattr(otel, "_provider", None)
    registered: list = []
    monkeypatch.setattr(atexit, "register", lambda fn, *a, **k: registered.append((fn, a)))

    tracer = otel.OTelTracingHandler()._get_tracer()
    assert tracer is not None
    # Never ended: nothing is queued for export, so shutdown needs no network.
    assert tracer.start_span("probe").is_recording() is True
    # One provider per process, however many handlers are built.
    assert otel.OTelTracingHandler()._get_tracer() is not None
    provider = otel._provider
    assert provider is not None
    # Flushed at exit, registered once (the SDK may add its own hook too).
    assert [r for r in registered if r[0] is otel._shutdown_provider] == [(otel._shutdown_provider, (provider,))]
    provider.shutdown()


def test_no_endpoint_means_no_tracer_even_with_the_sdk(monkeypatch) -> None:
    from nova.tracing import otel

    monkeypatch.setattr(otel, "_provider", None)
    assert otel.OTelTracingHandler()._get_tracer() is None
    assert otel._provider is None


def test_missing_sdk_warns_once_when_tracing_is_enabled(monkeypatch, caplog) -> None:
    import logging
    import sys

    from nova.tracing import otel

    monkeypatch.setenv("NOVA_OTEL_TRACING", "1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9/v1/traces")
    monkeypatch.setattr(otel, "_provider", None)
    monkeypatch.setattr(otel, "_sdk_missing_warned", False)
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace", None)

    caplog.set_level(logging.WARNING, logger="nova.tracing.otel")
    for _ in range(3):
        handler = otel.OTelTracingHandler()
        handler.on_chain_start({"name": "x"}, {}, run_id="r")
        handler.on_chain_end({}, run_id="r")
    warnings = [r for r in caplog.records if "could not be imported" in r.getMessage()]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
