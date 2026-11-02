"""ORM model registration entry point.

Importing this module ensures all ORM models are registered with
``Base.metadata`` so Alembic autogenerate detects every table.

The actual ORM classes have moved to entity-specific subpackages:
- ``nova.persistence.thread_meta``
- ``nova.persistence.run``
- ``nova.persistence.feedback``
- ``nova.persistence.user``

``RunEventRow`` remains in ``nova.persistence.models.run_event`` because
its storage implementation lives in ``nova.runtime.events.store.db`` and
there is no matching entity directory.
"""

from nova.persistence.admin_audit.model import AdminAuditRow
from nova.persistence.channel_connections.model import (
    ChannelConnectionRow,
    ChannelConversationRow,
    ChannelCredentialRow,
    ChannelOAuthStateRow,
)
from nova.persistence.credit_grant.model import CreditGrantRow
from nova.persistence.credit_request.model import CreditRequestRow
from nova.persistence.email_marketing.model import (
    EmBounceCursorRow,
    EmCampaignRow,
    EmContactRow,
    EmEventRow,
    EmListMemberRow,
    EmListRow,
    EmSendRow,
    EmSuppressionRow,
    EmTemplateRow,
)
from nova.persistence.feedback.model import FeedbackRow
from nova.persistence.harness_token.model import HarnessTokenRow
from nova.persistence.job.model import JobEventRow, JobRow, JobScheduleRow, JobWorkerRow
from nova.persistence.models.agent_config_row import AgentConfigRow
from nova.persistence.models.model_config_row import ModelConfigRow
from nova.persistence.models.run_event import RunEventRow
from nova.persistence.password_reset_token.model import PasswordResetTokenRow
from nova.persistence.run.model import RunRow
from nova.persistence.shared_thread.model import SharedThreadRow
from nova.persistence.thread_meta.model import ThreadMetaRow
from nova.persistence.user.model import UserRow
from nova.persistence.user_api_key.model import UserApiKeyRow

__all__ = [
    "EmBounceCursorRow",
    "EmCampaignRow",
    "EmContactRow",
    "EmEventRow",
    "EmListMemberRow",
    "EmListRow",
    "EmSendRow",
    "EmSuppressionRow",
    "EmTemplateRow",
    "JobEventRow",
    "JobRow",
    "JobScheduleRow",
    "JobWorkerRow",
    "AdminAuditRow",
    "AgentConfigRow",
    "ChannelConnectionRow",
    "ChannelConversationRow",
    "ChannelCredentialRow",
    "ChannelOAuthStateRow",
    "CreditGrantRow",
    "CreditRequestRow",
    "FeedbackRow",
    "HarnessTokenRow",
    "ModelConfigRow",
    "PasswordResetTokenRow",
    "RunEventRow",
    "RunRow",
    "SharedThreadRow",
    "ThreadMetaRow",
    "UserApiKeyRow",
    "UserRow",
]
