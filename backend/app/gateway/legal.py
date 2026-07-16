"""Legal document versions and consent constants.

Single source of truth for the current Terms of Service / Privacy Policy
version. Bumping ``TOS_VERSION`` invalidates every stored consent (the
frontend re-acceptance gate compares each user's ``tos_accepted_version``
against this value), forcing users to re-accept before continuing.

Versions are ISO dates on purpose: human-readable, monotonically
orderable, and trivially auditable in the ``users.tos_accepted_version``
column.
"""

from __future__ import annotations

# Bump this (and the corresponding terms/privacy page content) whenever the
# legal terms materially change. All existing consents become stale and the
# re-acceptance gate will prompt every user on their next authenticated load.
TOS_VERSION = "2026-07-15"
PRIVACY_VERSION = "2026-07-15"
