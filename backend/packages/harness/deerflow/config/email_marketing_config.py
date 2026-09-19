"""Email-marketing configuration (``email_marketing:`` in config.yaml).

Hot-reloaded: read per request / per job. Sending itself goes through the
``email:`` section's SMTP relay (Mailcow), so this block holds only what is
specific to campaigns: public links, tracking secret, throttles, the bounce
mailbox and retention.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class BounceMailboxConfig(BaseModel):
    host: str = Field(default="", description="IMAP host (Mailcow: host.docker.internal).")
    port: int = Field(default=993, description="IMAP port; 993 is implicit TLS.")
    use_ssl: bool = Field(default=True, description="Implicit TLS (IMAPS).")
    username: str = Field(default="", description="Mailbox that receives bounce+*/unsubscribe+* mail (e.g. bounces@example.com).")
    password: str = Field(default="", description="Mailbox password; `$ENV_VAR` resolves from the environment.")
    folder: str = Field(default="INBOX", description="Folder to poll.")


class EmailMarketingConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable email marketing (/api/em, the Email pages, the campaign jobs).")
    public_base_url: str = Field(default="", description="Public origin for tracking/unsubscribe links, e.g. https://nova.example.com. Required to send.")
    tracking_secret: str = Field(default="", description="HMAC secret for open/click/unsubscribe tokens; `$NOVA_EM_TRACKING_SECRET`. Required to send.")
    bounce_domain: str = Field(default="", description="Domain for VERP envelope senders (bounce+<send>@<domain>); defaults to the sender's domain.")
    default_throttle_per_minute: int = Field(default=120, ge=1, description="Messages per minute per campaign unless the campaign overrides it.")
    per_domain_per_minute: int = Field(default=20, ge=1, description="Messages per minute to any one recipient domain (Gmail, Outlook…).")
    batch_size: int = Field(default=50, ge=1, le=500, description="Sends per batch job.")
    bounce_mailbox: BounceMailboxConfig = Field(default_factory=BounceMailboxConfig)
    bounce_poll_cron: str = Field(default="*/5 * * * *", description="Cron for the IMAP bounce/complaint poll.")
    events_retention_days: int = Field(default=180, ge=1, description="Prune em_events older than this.")

    def send_ready(self) -> tuple[bool, str]:
        """Whether campaigns can actually go out, and why not."""
        if not self.enabled:
            return False, "email_marketing.enabled is false"
        if not self.public_base_url:
            return False, "email_marketing.public_base_url is not set"
        if not self.tracking_secret:
            return False, "email_marketing.tracking_secret is not set"
        return True, ""
