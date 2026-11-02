"""Local / self-hosted tracing over OTLP.

Why this exists
---------------
Nova's only tracing provider out of the box was LangSmith — a third-party
SaaS — and Langfuse, which needs a full self-hosted stack (web + worker +
ClickHouse + Postgres + Redis + MinIO) before it accepts a single span. The
2026-11-01 audit asked for "a more sophisticated local trace or OSS
self-hosting", and the honest answer is that the collector protocol is
already standard: **OTLP**. Every serious backend (Jaeger, Tempo, Grafana
Phlare, Langfuse v3, Honeycomb, Datadog) speaks it.

So this module speaks OTLP directly. Point ``OTEL_EXPORTER_OTLP_ENDPOINT``
at any local collector and the agent's spans — model calls, tool calls,
chain steps — land there with no vendor account, no API key, and no data
leaving the host.

What it is NOT
--------------
This is a *tracing* callback, not a replacement for the journal. The journal
(``runtime/journal.py``) is the run's durable record and the thing the
operator reads after the fact; this is the live span stream a local
collector can query. Both exist on purpose: a collector can be down without
losing the run record, and the run record cannot answer "what did the
second LLM call look like".

Span shape
----------
One span per LangChain callback event, named after the event
(``llm.chat_model``, ``chain.step``, ``tool.bash``), with the run's
identity as attributes (``run_id`` / ``thread_id`` / ``correlation_id``,
read from ``runtime.run_context``) and the token usage of an LLM call as
``gen_ai.usage.*`` attributes.

Failure policy
--------------
Tracing must never be able to fail a run. Every export path is wrapped,
the exporter's own errors are swallowed by the OTel SDK, and a missing or
unreachable collector degrades to "no spans", not to an exception in the
agent loop.
"""

from __future__ import annotations

import atexit
import logging
import os
import threading
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)

# Standard OTel env vars, read directly so this module works without the
# OpenTelemetry distro packages.
ENV_ENABLED = "NOVA_OTEL_TRACING"
ENV_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
ENV_HEADERS = "OTEL_EXPORTER_OTLP_HEADERS"
ENV_SERVICE_NAME = "OTEL_SERVICE_NAME"

_TRUTHY = {"1", "true", "yes", "on"}

# The process-wide provider this module owns. Private on purpose: the
# OpenTelemetry API's global (``trace._TRACER_PROVIDER``) always exists -- it
# starts as ``None`` and resolves to a proxy -- so a "has it been set?" check
# against the global can never be trusted. An earlier version tested
# ``hasattr(trace, "_TRACER_PROVIDER")``, which is always true, and so never
# installed an exporter: every span was a NonRecordingSpan. Owning the
# provider also means another library setting the global cannot silently
# reroute (or drop) Nova's spans.
_provider: Any | None = None
_provider_lock = threading.Lock()
_sdk_missing_warned = False


def _shutdown_provider(provider: Any) -> None:
    """Flush the last batch and stop the exporter at interpreter exit."""
    try:
        provider.force_flush()
    except Exception:
        pass
    try:
        provider.shutdown()
    except Exception:
        pass


def _build_provider(service_name: str, span_exporter: Any | None = None) -> Any | None:
    """A TracerProvider with one exporter, or None when the SDK is unavailable.

    ``span_exporter`` is injected by tests (an in-memory exporter behind a
    synchronous processor: no network, no batching delay); production uses
    the OTLP/HTTP exporter behind a batch processor.
    """
    global _sdk_missing_warned
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

        if span_exporter is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except Exception:
        # Only reached when the flag and an endpoint are set: the operator
        # expects spans, so this is not a debug detail. Once per process --
        # it would otherwise repeat for every handler.
        if not _sdk_missing_warned:
            _sdk_missing_warned = True
            logger.warning("OTLP tracing is enabled but the OpenTelemetry SDK/exporter could not be imported; no spans will be exported", exc_info=True)
        return None
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if span_exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    else:
        exporter = OTLPSpanExporter(
            endpoint=os.environ.get(ENV_ENDPOINT, ""),
            headers=_parse_headers(os.environ.get(ENV_HEADERS, "")),
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def _shared_provider(service_name: str) -> Any | None:
    """The process-wide provider, built once (thread-safe) on first use.

    Registered for shutdown at exit so the batch processor's last batch is
    flushed instead of lost with the process.
    """
    global _provider
    if _provider is not None:
        return _provider
    with _provider_lock:
        if _provider is None:
            provider = _build_provider(service_name)
            if provider is not None:
                atexit.register(_shutdown_provider, provider)
                _provider = provider
    return _provider


def otel_tracing_enabled() -> bool:
    """Whether OTLP tracing is switched on.

    Requires BOTH the flag and an endpoint: an exporter with no endpoint
    would silently drop every span, which reads as "tracing is on and
    working" while nothing is collected.
    """
    flag = (os.environ.get(ENV_ENABLED, "") or "").strip().lower() in _TRUTHY
    endpoint = (os.environ.get(ENV_ENDPOINT, "") or "").strip()
    return flag and bool(endpoint)


def _parse_headers(raw: str) -> dict[str, str]:
    """``key=value,key2=value2`` → headers. Malformed entries are skipped."""
    headers: dict[str, str] = {}
    for pair in raw.split(","):
        if "=" not in pair:
            continue
        key, _, value = pair.partition("=")
        key = key.strip()
        if key:
            headers[key] = value.strip()
    return headers


class OTelTracingHandler(BaseCallbackHandler):
    """A LangChain callback that exports spans over OTLP.

    Implements the events that matter for an agent run: model calls (with
    their token usage), tool calls, and chain steps. Everything else is
    ignored — a span per event is a lot of spans, and the ones listed are
    the ones an operator actually looks for after an incident.
    """

    # Sync callbacks are dispatched to a thread-pool thread by LangChain
    # unless they opt in. The OTel SDK is thread-safe either way, but the
    # spans then carry the wrong context when the parent span lives on the
    # event loop; inline keeps the span hierarchy intact.
    run_inline = True

    def __init__(self, *, service_name: str | None = None, tracer: Any | None = None, span_exporter: Any | None = None) -> None:
        """``tracer`` replaces the tracer outright; ``span_exporter`` keeps the
        real SDK but exports through a provider private to this handler (an
        in-memory exporter in tests). Production passes neither."""
        super().__init__()
        self._service_name = service_name or os.environ.get(ENV_SERVICE_NAME, "nova")
        self._tracer = tracer
        self._span_exporter = span_exporter
        self._provider: Any | None = None
        self._spans: dict[str, Any] = {}

    # -- lifecycle --

    def _get_tracer(self) -> Any | None:
        """The OTel tracer, or None when tracing cannot export anything.

        Built lazily so that importing this module never requires the
        OpenTelemetry packages: a deployment without them must still start.
        The tracer comes from a provider this module owns, never from the
        API's global (see ``_provider``).
        """
        if self._tracer is not None:
            return self._tracer
        try:
            if self._span_exporter is not None:
                if self._provider is None:
                    self._provider = _build_provider(self._service_name, self._span_exporter)
                provider = self._provider
            elif otel_tracing_enabled():
                provider = _shared_provider(self._service_name)
            else:
                # No endpoint: an exporter would drop every span.
                provider = None
            if provider is None:
                return None
            self._tracer = provider.get_tracer(self._service_name)
        except Exception:
            logger.warning("Could not initialise the OTLP tracer; tracing disabled", exc_info=True)
            return None
        return self._tracer

    def _start(self, name: str, run_id: Any, parent_run_id: Any, attributes: dict[str, Any]) -> Any | None:
        tracer = self._get_tracer()
        if tracer is None:
            return None
        try:
            from opentelemetry import context as otel_context
            from opentelemetry import trace

            key = str(run_id)
            parent = self._spans.get(str(parent_run_id)) if parent_run_id else None
            ctx = trace.set_span_in_context(parent) if parent is not None else otel_context.get_current()
            span = tracer.start_span(name, context=ctx, attributes={k: v for k, v in attributes.items() if v is not None})
            self._spans[key] = span
            return span
        except Exception:
            logger.debug("Could not start OTLP span %s", name, exc_info=True)
            return None

    def _end(self, run_id: Any, attributes: dict[str, Any] | None = None) -> None:
        span = self._spans.pop(str(run_id), None)
        if span is None:
            return
        try:
            for key, value in (attributes or {}).items():
                if value is not None:
                    span.set_attribute(key, value)
            span.end()
        except Exception:
            logger.debug("Could not end OTLP span", exc_info=True)

    # -- run identity --

    @staticmethod
    def _run_attributes() -> dict[str, Any]:
        try:
            from nova.runtime.run_context import current_run_fields

            return current_run_fields()
        except Exception:
            return {}

    # -- LangChain events --

    def on_chain_start(self, serialized: dict, inputs: dict, *, run_id: Any, parent_run_id: Any = None, tags: list[str] | None = None, **kwargs: Any) -> None:
        name = (serialized or {}).get("name") or "chain"
        self._start(f"chain.{name}", run_id, parent_run_id, {**self._run_attributes(), "langchain.chain.name": name})

    def on_chain_end(self, outputs: dict, *, run_id: Any, **kwargs: Any) -> None:
        self._end(run_id)

    def on_chain_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        # Record first: ``_end`` pops the span, after which there is nothing
        # left to record the error on.
        self._record_error(error, run_id)
        self._end(run_id, {"error.type": type(error).__name__, "error.message": str(error)[:500]})

    def on_llm_start(self, serialized: dict, prompts: list[str], *, run_id: Any, parent_run_id: Any = None, **kwargs: Any) -> None:
        model = (serialized or {}).get("name") or "llm"
        self._start(f"llm.{model}", run_id, parent_run_id, {**self._run_attributes(), "gen_ai.system": "nova", "gen_ai.request.model": model})

    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        attributes: dict[str, Any] = {}
        try:
            for generation in getattr(response, "generations", []) or []:
                for gen in generation:
                    message = getattr(gen, "message", None)
                    usage = getattr(message, "usage_metadata", None)
                    if usage:
                        attributes["gen_ai.usage.input_tokens"] = usage.get("input_tokens")
                        attributes["gen_ai.usage.output_tokens"] = usage.get("output_tokens")
                        attributes["gen_ai.usage.total_tokens"] = usage.get("total_tokens")
                    meta = getattr(message, "response_metadata", None) or {}
                    if isinstance(meta, dict) and (meta.get("model_name") or meta.get("model")):
                        attributes["gen_ai.response.model"] = meta.get("model_name") or meta.get("model")
                    break
                break
        except Exception:
            logger.debug("Could not read LLM usage for the span", exc_info=True)
        self._end(run_id, attributes)

    def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        # Record first: ``_end`` pops the span, after which there is nothing
        # left to record the error on.
        self._record_error(error, run_id)
        self._end(run_id, {"error.type": type(error).__name__, "error.message": str(error)[:500]})

    def on_tool_start(self, serialized: dict, input_str: str, *, run_id: Any, parent_run_id: Any = None, tags: list[str] | None = None, **kwargs: Any) -> None:
        name = (serialized or {}).get("name") or "tool"
        self._start(f"tool.{name}", run_id, parent_run_id, {**self._run_attributes(), "gen_ai.tool.name": name})

    def on_tool_end(self, output: Any, *, run_id: Any, **kwargs: Any) -> None:
        self._end(run_id)

    def on_tool_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        # Record first: ``_end`` pops the span, after which there is nothing
        # left to record the error on.
        self._record_error(error, run_id)
        self._end(run_id, {"error.type": type(error).__name__, "error.message": str(error)[:500]})

    def _record_error(self, error: BaseException, run_id: Any) -> None:
        """Record an exception event on the span (OTel convention)."""
        span = self._spans.get(str(run_id))
        if span is None:
            return
        try:
            from opentelemetry.trace import Status, StatusCode

            span.record_exception(error)
            span.set_status(Status(StatusCode.ERROR, str(error)[:500]))
        except Exception:
            logger.debug("Could not record the exception on the span", exc_info=True)
