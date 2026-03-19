"""Tests for roost.services.credentials — encrypted credential storage."""

import pytest


class TestStoreAndRetrieve:
    """Round-trip: store → get → masked → delete."""

    def test_store_and_get(self, db_cleanup):
        from roost.services.credentials import store_credential, get_credential
        store_credential("TEST_KEY_1", "super-secret-value", user_id=1)
        assert get_credential("TEST_KEY_1", user_id=1) == "super-secret-value"

    def test_get_nonexistent_returns_none(self):
        from roost.services.credentials import get_credential
        assert get_credential("DOES_NOT_EXIST", user_id=1) is None

    def test_masked_short_value(self, db_cleanup):
        from roost.services.credentials import store_credential, get_credential_masked
        store_credential("TEST_SHORT", "abcd", user_id=1)
        assert get_credential_masked("TEST_SHORT", user_id=1) == "****"

    def test_masked_long_value(self, db_cleanup):
        from roost.services.credentials import store_credential, get_credential_masked
        store_credential("TEST_LONG", "sk-ant-api03-abcdef123456", user_id=1)
        masked = get_credential_masked("TEST_LONG", user_id=1)
        assert masked.startswith("sk-a")
        assert masked.endswith("3456")
        assert "••••" in masked

    def test_masked_nonexistent_returns_none(self):
        from roost.services.credentials import get_credential_masked
        assert get_credential_masked("NOPE", user_id=1) is None

    def test_overwrite_credential(self, db_cleanup):
        from roost.services.credentials import store_credential, get_credential
        store_credential("TEST_OVERWRITE", "old-value", user_id=1)
        store_credential("TEST_OVERWRITE", "new-value", user_id=1)
        assert get_credential("TEST_OVERWRITE", user_id=1) == "new-value"


class TestDelete:
    """Credential deletion."""

    def test_delete_existing(self, db_cleanup):
        from roost.services.credentials import store_credential, delete_credential, get_credential
        store_credential("TEST_DEL", "to-delete", user_id=1)
        assert delete_credential("TEST_DEL", user_id=1) is True
        assert get_credential("TEST_DEL", user_id=1) is None

    def test_delete_nonexistent(self):
        from roost.services.credentials import delete_credential
        assert delete_credential("NEVER_STORED", user_id=1) is False


class TestUserIsolation:
    """Credentials are scoped per user_id."""

    def test_different_users_different_values(self, db_cleanup):
        from roost.services.credentials import store_credential, get_credential
        store_credential("SHARED_KEY", "user1-secret", user_id=1)
        store_credential("SHARED_KEY", "user2-secret", user_id=2)
        assert get_credential("SHARED_KEY", user_id=1) == "user1-secret"
        assert get_credential("SHARED_KEY", user_id=2) == "user2-secret"


class TestEncryption:
    """Verify values are actually encrypted in storage."""

    def test_raw_value_not_in_db(self, db_cleanup):
        from roost.services.credentials import store_credential
        from roost.services.settings import get_setting

        plaintext = "my-plaintext-api-key-12345"
        store_credential("TEST_ENC", plaintext, user_id=1)

        # Read the raw stored value — it should be encrypted, not plaintext
        raw = get_setting("credential:TEST_ENC", user_id=1)
        assert raw is not None
        assert raw != plaintext
        assert len(raw) > len(plaintext)  # Fernet adds overhead


class TestTestCredential:
    """test_credential() for unknown integrations."""

    def test_unknown_integration(self):
        from roost.services.credentials import test_credential
        result = test_credential("nonexistent_service", user_id=1)
        assert result["ok"] is False
        assert "Unknown" in result["detail"]

    def test_missing_credential(self):
        from roost.services.credentials import test_credential
        # Telegram with no stored token
        result = test_credential("telegram", user_id=999)
        assert result["ok"] is False
        assert "No" in result["detail"]
