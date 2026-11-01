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

import logging
import os
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

    def __init__(self, *, service_name: str | None = None, tracer: Any | None = None) -> None:
        super().__init__()
        self._service_name = service_name or os.environ.get(ENV_SERVICE_NAME, "nova")
        self._tracer = tracer
        self._spans: dict[str, Any] = {}
        self._shutdown_done = False

    # -- lifecycle --

    def _get_tracer(self) -> Any | None:
        """The OTel tracer, or None when the SDK is unavailable.

        Built lazily so that importing this module never requires the
        OpenTelemetry packages: a deployment without them must still start.
        """
        if self._tracer is not None:
            return self._tracer
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except Exception:
            logger.debug("OpenTelemetry SDK not installed; OTLP tracing disabled", exc_info=True)
            return None
        try:
            if not hasattr(trace, "_TRACER_PROVIDER"):
                provider = TracerProvider(
                    resource=Resource.create({"service.name": self._service_name}),
                )
                exporter = OTLPSpanExporter(
                    endpoint=os.environ.get(ENV_ENDPOINT, ""),
                    headers=_parse_headers(os.environ.get(ENV_HEADERS, "")),
                )
                provider.add_span_processor(BatchSpanProcessor(exporter))
                trace.set_tracer_provider(provider)
            self._tracer = trace.get_tracer(self._service_name)
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
            from deerflow.runtime.run_context import current_run_fields

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
        self._end(run_id, {"error.type": type(error).__name__, "error.message": str(error)[:500]})
        self._record_error(error, run_id)

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
        self._end(run_id, {"error.type": type(error).__name__, "error.message": str(error)[:500]})
        self._record_error(error, run_id)

    def on_tool_start(self, serialized: dict, input_str: str, *, run_id: Any, parent_run_id: Any = None, tags: list[str] | None = None, **kwargs: Any) -> None:
        name = (serialized or {}).get("name") or "tool"
        self._start(f"tool.{name}", run_id, parent_run_id, {**self._run_attributes(), "gen_ai.tool.name": name})

    def on_tool_end(self, output: Any, *, run_id: Any, **kwargs: Any) -> None:
        self._end(run_id)

    def on_tool_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        self._end(run_id, {"error.type": type(error).__name__, "error.message": str(error)[:500]})
        self._record_error(error, run_id)

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
