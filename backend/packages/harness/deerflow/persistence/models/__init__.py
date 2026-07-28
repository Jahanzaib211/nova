"""ORM model registration entry point.

Importing this module ensures all ORM models are registered with
``Base.metadata`` so Alembic autogenerate detects every table.

The actual ORM classes have moved to entity-specific subpackages:
- ``deerflow.persistence.thread_meta``
- ``deerflow.persistence.run``
- ``deerflow.persistence.feedback``
- ``deerflow.persistence.user``

``RunEventRow`` remains in ``deerflow.persistence.models.run_event`` because
its storage implementation lives in ``deerflow.runtime.events.store.db`` and
there is no matching entity directory.
"""

from deerflow.persistence.admin_audit.model import AdminAuditRow
from deerflow.persistence.channel_connections.model import (
    ChannelConnectionRow,
    ChannelConversationRow,
    ChannelCredentialRow,
    ChannelOAuthStateRow,
)
from deerflow.persistence.credit_grant.model import CreditGrantRow
from deerflow.persistence.credit_request.model import CreditRequestRow
from deerflow.persistence.feedback.model import FeedbackRow
from deerflow.persistence.models.agent_config_row import AgentConfigRow
from deerflow.persistence.models.model_config_row import ModelConfigRow
from deerflow.persistence.models.run_event import RunEventRow
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.user.model import UserRow
from deerflow.persistence.user_api_key.model import UserApiKeyRow

__all__ = [
    "AdminAuditRow",
    "AgentConfigRow",
    "ChannelConnectionRow",
    "ChannelConversationRow",
    "ChannelCredentialRow",
    "ChannelOAuthStateRow",
    "CreditGrantRow",
    "CreditRequestRow",
    "FeedbackRow",
    "ModelConfigRow",
    "RunEventRow",
    "RunRow",
    "ThreadMetaRow",
    "UserApiKeyRow",
    "UserRow",
]
