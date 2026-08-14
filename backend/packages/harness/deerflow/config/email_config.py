"""SMTP email configuration for the ``email:`` section of config.yaml.

Enables the forgot-password self-service reset link. When ``enabled`` is
false the ``forgot-password`` endpoint answers with a
``password_reset_disabled`` error instead of attempting delivery.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class EmailConfig(BaseModel):
    """Outbound SMTP settings. Values starting with ``$`` resolve as env vars."""

    enabled: bool = Field(
        default=False,
        description=("Master switch for outbound email. When false, password-reset emails are never sent and /auth/forgot-password reports password_reset_disabled."),
    )
    host: str = Field(default="", description="SMTP server hostname")
    port: int = Field(default=587, description="SMTP server port (587 for STARTTLS, 465 for implicit TLS)")
    use_tls: bool = Field(
        default=True,
        description="Use STARTTLS after connecting (true for port 587). Set false with use_ssl for implicit TLS.",
    )
    use_ssl: bool = Field(default=False, description="Use implicit TLS from the first byte (port 465).")
    username: str = Field(default="", description="SMTP auth username")
    password: str = Field(default="", description="SMTP auth password (may be `$ENV_VAR_NAME`)")
    from_email: str = Field(default="", description="Sender address (required when enabled)")
    from_name: str = Field(default="Nova", description="Display name of the sender")
    reset_link_base_url: str = Field(
        default="",
        description=("Public origin used to build the reset link, e.g. https://nova.example.com. Empty uses the request's own base URL."),
    )
