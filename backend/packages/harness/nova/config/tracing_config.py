import os
import threading

from pydantic import BaseModel, Field

_config_lock = threading.Lock()


class LangSmithTracingConfig(BaseModel):
    """Configuration for LangSmith tracing."""

    enabled: bool = Field(...)
    api_key: str | None = Field(...)
    project: str = Field(...)
    endpoint: str = Field(...)

    @property
    def is_configured(self) -> bool:
        return self.enabled and bool(self.api_key)

    def validate(self) -> None:
        if self.enabled and not self.api_key:
            raise ValueError("LangSmith tracing is enabled but LANGSMITH_API_KEY (or LANGCHAIN_API_KEY) is not set.")


class LangfuseTracingConfig(BaseModel):
    """Configuration for Langfuse tracing."""

    enabled: bool = Field(...)
    public_key: str | None = Field(...)
    secret_key: str | None = Field(...)
    host: str = Field(...)

    @property
    def is_configured(self) -> bool:
        return self.enabled and bool(self.public_key) and bool(self.secret_key)

    def validate(self) -> None:
        if not self.enabled:
            return
        missing: list[str] = []
        if not self.public_key:
            missing.append("LANGFUSE_PUBLIC_KEY")
        if not self.secret_key:
            missing.append("LANGFUSE_SECRET_KEY")
        if missing:
            raise ValueError(f"Langfuse tracing is enabled but required settings are missing: {', '.join(missing)}")


class OTelTracingConfig(BaseModel):
    """Configuration for OTLP (local / self-hosted) tracing.

    The collector protocol is the standard one, so the same configuration
    points at Jaeger, Tempo, Grafana Phlare, a Langfuse v3 instance, or any
    other OTLP endpoint — with no data leaving the host.

    ``enabled`` means "the flag is set AND an endpoint is known". Unlike
    LangSmith (whose explicit flag without a key is a misconfiguration worth
    reporting), an OTLP flag without an endpoint is the *expected* state of
    "tracing switched on but the collector is not configured yet" — and it
    must not raise: validation runs inside the agent's build path, so an
    error here would stop the agent from starting.
    """

    enabled: bool = Field(...)
    endpoint: str = Field(...)
    service_name: str = Field(...)

    @property
    def is_configured(self) -> bool:
        return self.enabled and bool(self.endpoint)

    def validate(self) -> None:
        # Never raises. An enabled-but-endpointless OTLP provider is simply
        # not built (see build_tracing_callbacks), and a missing collector
        # must degrade to "no spans", never to a failed agent start.
        return


class TracingConfig(BaseModel):
    """Tracing configuration for supported providers."""

    langsmith: LangSmithTracingConfig = Field(...)
    langfuse: LangfuseTracingConfig = Field(...)
    otel: OTelTracingConfig = Field(...)

    @property
    def is_configured(self) -> bool:
        return bool(self.enabled_providers)

    @property
    def explicitly_enabled_providers(self) -> list[str]:
        enabled: list[str] = []
        if self.langsmith.enabled:
            enabled.append("langsmith")
        if self.langfuse.enabled:
            enabled.append("langfuse")
        if self.otel.enabled:
            enabled.append("otel")
        return enabled

    @property
    def enabled_providers(self) -> list[str]:
        enabled: list[str] = []
        if self.langsmith.is_configured:
            enabled.append("langsmith")
        if self.langfuse.is_configured:
            enabled.append("langfuse")
        if self.otel.is_configured:
            enabled.append("otel")
        return enabled

    def validate_enabled(self) -> None:
        self.langsmith.validate()
        self.langfuse.validate()
        self.otel.validate()


_tracing_config: TracingConfig | None = None


_TRUTHY_VALUES = {"1", "true", "yes", "on"}


def _env_flag_preferred(*names: str) -> bool:
    """Return the boolean value of the first env var that is present and non-empty."""
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip().lower() in _TRUTHY_VALUES
    return False


def _first_env_value(*names: str) -> str | None:
    """Return the first non-empty environment value from candidate names."""
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def get_tracing_config() -> TracingConfig:
    """Get the current tracing configuration from environment variables."""
    global _tracing_config
    if _tracing_config is not None:
        return _tracing_config
    with _config_lock:
        if _tracing_config is not None:
            return _tracing_config
        _tracing_config = TracingConfig(
            langsmith=LangSmithTracingConfig(
                enabled=_env_flag_preferred("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"),
                api_key=_first_env_value("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"),
                project=_first_env_value("LANGSMITH_PROJECT", "LANGCHAIN_PROJECT") or "deer-flow",
                endpoint=_first_env_value("LANGSMITH_ENDPOINT", "LANGCHAIN_ENDPOINT") or "https://api.smith.langchain.com",
            ),
            langfuse=LangfuseTracingConfig(
                enabled=_env_flag_preferred("LANGFUSE_TRACING"),
                public_key=_first_env_value("LANGFUSE_PUBLIC_KEY"),
                secret_key=_first_env_value("LANGFUSE_SECRET_KEY"),
                host=_first_env_value("LANGFUSE_BASE_URL") or "https://cloud.langfuse.com",
            ),
            otel=OTelTracingConfig(
                # Local / self-hosted tracing: a standard OTLP collector on
                # this host (or any reachable one). Nothing leaves the box
                # unless the operator points it somewhere else. The provider
                # counts as enabled only when BOTH the flag and an endpoint
                # are present — a flag alone has nowhere to send the spans.
                enabled=(_env_flag_preferred("NOVA_OTEL_TRACING", "OTEL_TRACING") and bool(_first_env_value("OTEL_EXPORTER_OTLP_ENDPOINT"))),
                endpoint=_first_env_value("OTEL_EXPORTER_OTLP_ENDPOINT") or "",
                service_name=_first_env_value("OTEL_SERVICE_NAME") or "nova",
            ),
        )
        return _tracing_config


def get_enabled_tracing_providers() -> list[str]:
    """Return the configured tracing providers that are enabled and complete."""
    return get_tracing_config().enabled_providers


def get_explicitly_enabled_tracing_providers() -> list[str]:
    """Return tracing providers explicitly enabled by config, even if incomplete."""
    return get_tracing_config().explicitly_enabled_providers


def validate_enabled_tracing_providers() -> None:
    """Validate that any explicitly enabled providers are fully configured."""
    get_tracing_config().validate_enabled()


def is_tracing_enabled() -> bool:
    """Check if any tracing provider is enabled and fully configured."""
    return get_tracing_config().is_configured


def reset_tracing_config() -> None:
    """Discard the cached :class:`TracingConfig` so the next call rebuilds it.

    Public API so that tests do not have to reach into the private
    ``_tracing_config`` module attribute. A future internal rename would
    silently break callers that mutate the attribute directly.
    """
    global _tracing_config
    with _config_lock:
        _tracing_config = None
