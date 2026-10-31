import os
import threading

from pydantic import BaseModel, Field


class GatewayConfig(BaseModel):
    """Configuration for the API Gateway."""

    host: str = Field(default="0.0.0.0", description="Host to bind the gateway server")
    port: int = Field(default=8001, description="Port to bind the gateway server")
    enable_docs: bool = Field(default=True, description="Enable Swagger/ReDoc/OpenAPI endpoints")


_gateway_config: GatewayConfig | None = None
_gateway_config_env: tuple[str, str, str] | None = None
_gateway_config_lock = threading.Lock()


def _env_signature() -> tuple[str, str, str]:
    return (
        os.getenv("GATEWAY_HOST", "0.0.0.0"),
        os.getenv("GATEWAY_PORT", "8001"),
        os.getenv("GATEWAY_ENABLE_DOCS", "true"),
    )


def get_gateway_config() -> GatewayConfig:
    """Get gateway config, rebuilding it when the relevant env vars change.

    Unlike AppConfig, which reloads on file mtime, GatewayConfig is three
    fields read from the environment. Caching the resolved values *and the
    signature they were built from* means a change to GATEWAY_ENABLE_DOCS (or
    host/port) takes effect on the next call rather than at the next restart.

    The signature comparison is the whole point: an earlier version computed
    it and then returned the cache unconditionally, so the documented reload
    never happened and every call paid for three discarded getenv lookups.
    """
    global _gateway_config, _gateway_config_env
    signature = _env_signature()
    cached = _gateway_config
    if cached is not None and _gateway_config_env == signature:
        return cached
    with _gateway_config_lock:
        # Re-check under the lock: another thread may have rebuilt it.
        if _gateway_config is not None and _gateway_config_env == signature:
            return _gateway_config
        host, port, enable_docs = signature
        _gateway_config = GatewayConfig(
            host=host,
            port=int(port),
            enable_docs=enable_docs.lower() == "true",
        )
        _gateway_config_env = signature
        return _gateway_config
