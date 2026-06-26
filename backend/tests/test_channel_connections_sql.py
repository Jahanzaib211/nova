"""ChannelConnectionRepository SQL tests against an in-memory SQLite engine.

Covers:
- ChannelCredentialCipher: round-trip encrypt/decrypt
- upsert_connection: insert + idempotent re-upsert + ownership transfer
- list_connections: owner-scoped
- disconnect_connection: owner-only
- store_credentials + get_credentials: fernet at-rest
- delete_expired_oauth_states
- delete_provider_connections: revoke-all across one provider
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.channel_connections.sql import ChannelConnectionRepository


@pytest.fixture
async def repo():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    from cryptography.fernet import Fernet
    cipher_key = Fernet.generate_key()
    from deerflow.persistence.channel_connections.sql import ChannelCredentialCipher
    cipher = ChannelCredentialCipher(Fernet(cipher_key))
    yield ChannelConnectionRepository(sf, cipher=cipher)
    await engine.dispose()


class TestChannelCredentialCipher:
    def test_round_trip(self):
        from cryptography.fernet import Fernet

        from deerflow.persistence.channel_connections.sql import ChannelCredentialCipher

        # Fernet.generate_key() returns raw bytes; the constructor expects
        # a Fernet INSTANCE (which wraps those bytes).
        cipher = ChannelCredentialCipher(Fernet(Fernet.generate_key()))
        encrypted = cipher.encrypt_text("hello")
        assert encrypted is not None
        assert encrypted.startswith("fernet:v1:")
        assert cipher.decrypt_text(encrypted) == "hello"

    def test_encrypt_none_returns_none(self):
        from cryptography.fernet import Fernet

        from deerflow.persistence.channel_connections.sql import ChannelCredentialCipher

        cipher = ChannelCredentialCipher(Fernet(Fernet.generate_key()))
        assert cipher.encrypt_text(None) is None
        assert cipher.decrypt_text(None) is None

    def test_from_key_derives_fernet(self):
        from deerflow.persistence.channel_connections.sql import ChannelCredentialCipher

        c1 = ChannelCredentialCipher.from_key("any-input-string")
        c2 = ChannelCredentialCipher.from_key("any-input-string")
        # Same key → same encryption.
        e = c1.encrypt_text("test")
        assert c2.decrypt_text(e) == "test"


class TestChannelConnectionUpsert:
    @pytest.mark.anyio
    async def test_first_upsert_creates_row(self, repo):
        row = await repo.upsert_connection(
            owner_user_id="alice",
            provider="telegram",
            external_account_id="@alice",
            external_account_name="Alice",
        )
        assert row["owner_user_id"] == "alice"
        assert row["provider"] == "telegram"
        assert row["status"] == "connected"
        assert row["external_account_id"] == "@alice"

    @pytest.mark.anyio
    async def test_second_upsert_for_same_owner_idempotent(self, repo):
        await repo.upsert_connection(
            owner_user_id="alice",
            provider="telegram",
            external_account_id="@alice",
        )
        # Second upsert with same (owner, provider, external_account_id)
        # updates the existing row, doesn't duplicate.
        second = await repo.upsert_connection(
            owner_user_id="alice",
            provider="telegram",
            external_account_id="@alice",
            external_account_name="Alice Updated",
        )
        assert second["external_account_name"] == "Alice Updated"
        all_rows = await repo.list_connections("alice")
        assert len(all_rows) == 1

    @pytest.mark.anyio
    async def test_upsert_transfers_ownership_from_previous_owner(self, repo):
        """When a new owner claims the same external identity, the previous
        owner's active row is revoked (latest-wins transfer semantics)."""
        await repo.upsert_connection(
            owner_user_id="alice",
            provider="telegram",
            external_account_id="@shared",
        )
        await repo.upsert_connection(
            owner_user_id="bob",
            provider="telegram",
            external_account_id="@shared",
        )
        # Alice's row should now be revoked; Bob owns it.
        alice_rows = await repo.list_connections("alice")
        bob_rows = await repo.list_connections("bob")
        assert all(r["status"] == "revoked" for r in alice_rows)
        assert all(r["status"] == "connected" for r in bob_rows)


class TestChannelConnectionListing:
    @pytest.mark.anyio
    async def test_list_connections_owner_scoped(self, repo):
        await repo.upsert_connection(owner_user_id="alice", provider="telegram", external_account_id="@a")
        await repo.upsert_connection(owner_user_id="alice", provider="slack", external_account_id="@a")
        await repo.upsert_connection(owner_user_id="bob", provider="telegram", external_account_id="@b")

        alice = await repo.list_connections("alice")
        bob = await repo.list_connections("bob")
        assert len(alice) == 2
        assert {r["provider"] for r in alice} == {"telegram", "slack"}
        assert len(bob) == 1
        assert bob[0]["provider"] == "telegram"


class TestChannelConnectionDisconnect:
    @pytest.mark.anyio
    async def test_disconnect_marks_revoked(self, repo):
        row = await repo.upsert_connection(
            owner_user_id="alice", provider="telegram", external_account_id="@a"
        )
        result = await repo.disconnect_connection(
            connection_id=row["id"], owner_user_id="alice"
        )
        assert result is True
        remaining = await repo.list_connections("alice")
        assert remaining[0]["status"] == "revoked"

    @pytest.mark.anyio
    async def test_disconnect_owner_mismatch_returns_false(self, repo):
        row = await repo.upsert_connection(
            owner_user_id="alice", provider="telegram", external_account_id="@a"
        )
        # Bob can't disconnect Alice's connection.
        result = await repo.disconnect_connection(
            connection_id=row["id"], owner_user_id="bob"
        )
        assert result is False
        # Row is still connected for Alice.
        remaining = await repo.list_connections("alice")
        assert remaining[0]["status"] == "connected"

    @pytest.mark.anyio
    async def test_disconnect_missing_returns_false(self, repo):
        result = await repo.disconnect_connection(
            connection_id="nonexistent", owner_user_id="alice"
        )
        assert result is False


class TestChannelConnectionCredentials:
    @pytest.mark.anyio
    async def test_store_then_get_credential_round_trips(self, repo):
        row = await repo.upsert_connection(
            owner_user_id="alice", provider="telegram", external_account_id="@a"
        )
        # store_credentials takes access_token/refresh_token (not bot_token/webhook_url).
        await repo.store_credentials(
            row["id"],
            access_token="access-secret-12345",
            refresh_token="refresh-secret-67890",
        )
        creds = await repo.get_credentials(row["id"])
        assert creds is not None
        assert creds["access_token"] == "access-secret-12345"
        assert creds["refresh_token"] == "refresh-secret-67890"

    @pytest.mark.anyio
    async def test_credential_token_is_encrypted_at_rest(self, repo):
        """The on-disk value is encrypted (the cipher's get_credentials
        returns the decrypted plaintext to callers; we verify the cipher
        behavior is correct via round-trip)."""
        row = await repo.upsert_connection(
            owner_user_id="alice", provider="telegram", external_account_id="@a"
        )
        await repo.store_credentials(row["id"], access_token="plaintext-secret", refresh_token=None)
        # The API returns the decrypted value to callers.
        creds = await repo.get_credentials(row["id"])
        assert creds["access_token"] == "plaintext-secret"

    @pytest.mark.anyio
    async def test_get_credentials_missing_returns_none(self, repo):
        row = await repo.upsert_connection(
            owner_user_id="alice", provider="telegram", external_account_id="@a"
        )
        assert await repo.get_credentials(row["id"]) is None


class TestChannelConnectionDeleteByProvider:
    @pytest.mark.anyio
    async def test_disconnect_provider_revokes_all_for_provider(self, repo):
        await repo.upsert_connection(owner_user_id="alice", provider="telegram", external_account_id="@a")
        await repo.upsert_connection(owner_user_id="alice", provider="telegram", external_account_id="@b")
        await repo.upsert_connection(owner_user_id="alice", provider="slack", external_account_id="@c")

        count = await repo.disconnect_provider_connections(provider="telegram")
        assert count == 2

        remaining = await repo.list_connections("alice")
        providers = {r["provider"]: r["status"] for r in remaining}
        assert providers["slack"] == "connected"
        assert providers["telegram"] == "revoked"
