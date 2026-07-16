"""User repository interface for abstracting database operations."""

from abc import ABC, abstractmethod
from datetime import datetime

from app.gateway.auth.models import User


class UserNotFoundError(LookupError):
    """Raised when a user repository operation targets a non-existent row.

    Subclass of :class:`LookupError` so callers that already catch
    ``LookupError`` for "missing entity" can keep working unchanged,
    while specific call sites can pin to this class to distinguish
    "concurrent delete during update" from other lookups.
    """


class UserRepository(ABC):
    """Abstract interface for user data storage.

    Implement this interface to support different storage backends
    (SQLite)
    """

    @abstractmethod
    async def create_user(self, user: User) -> User:
        """Create a new user.

        Args:
            user: User object to create

        Returns:
            Created User with ID assigned

        Raises:
            ValueError: If email already exists
        """
        raise NotImplementedError

    @abstractmethod
    async def get_user_by_id(self, user_id: str) -> User | None:
        """Get user by ID.

        Args:
            user_id: User UUID as string

        Returns:
            User if found, None otherwise
        """
        raise NotImplementedError

    @abstractmethod
    async def get_user_by_email(self, email: str) -> User | None:
        """Get user by email.

        Args:
            email: User email address

        Returns:
            User if found, None otherwise
        """
        raise NotImplementedError

    @abstractmethod
    async def update_user(self, user: User) -> User:
        """Update an existing user.

        Args:
            user: User object with updated fields

        Returns:
            Updated User

        Raises:
            UserNotFoundError: If no row exists for ``user.id``. This is
                a hard failure (not a no-op) so callers cannot mistake a
                concurrent-delete race for a successful update.
        """
        raise NotImplementedError

    @abstractmethod
    async def count_users(self) -> int:
        """Return total number of registered users."""
        raise NotImplementedError

    @abstractmethod
    async def count_admin_users(self) -> int:
        """Return number of users with system_role == 'admin'."""
        raise NotImplementedError

    @abstractmethod
    async def list_users(self, *, limit: int, offset: int) -> list[User]:
        """Return a page of users, newest first (by created_at).

        Args:
            limit: Maximum number of users to return.
            offset: Number of users to skip (for pagination).
        """
        raise NotImplementedError

    @abstractmethod
    async def count_users_since(self, since: datetime) -> int:
        """Return the number of users created at or after ``since``."""
        raise NotImplementedError

    @abstractmethod
    async def count_users_by_plan(self) -> dict[str, int]:
        """Return a mapping of plan name → user count."""
        raise NotImplementedError

    @abstractmethod
    async def get_user_by_referral_code(self, code: str) -> User | None:
        """Return the user whose ``referral_code`` equals ``code``, if any."""
        raise NotImplementedError

    @abstractmethod
    async def count_users_referred_by(self, code: str) -> int:
        """Return how many users were referred by the given referral code."""
        raise NotImplementedError

    @abstractmethod
    async def get_user_by_stripe_customer_id(self, customer_id: str) -> User | None:
        """Return the user linked to the given Stripe customer id, if any."""
        raise NotImplementedError

    @abstractmethod
    async def reset_all_usage(self, reset_at: datetime) -> int:
        """Stamp ``credit_usage_reset_at = reset_at`` on every user.

        Returns the number of rows affected. Used by the ops console's
        bulk "reset all usage" action.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_user_by_oauth(self, provider: str, oauth_id: str) -> User | None:
        """Get user by OAuth provider and ID.

        Args:
            provider: OAuth provider name (e.g. 'github', 'google')
            oauth_id: User ID from the OAuth provider

        Returns:
            User if found, None otherwise
        """
        raise NotImplementedError
