"""Tests for shared validation utilities."""
import pytest

from supabase_client import validate_uuid, validate_limit, validate_no_secrets


class TestValidateUuid:
    def test_valid_uuid(self):
        result = validate_uuid("550e8400-e29b-41d4-a716-446655440000")
        assert result == "550e8400-e29b-41d4-a716-446655440000"

    def test_valid_uuid_uppercase(self):
        result = validate_uuid("550E8400-E29B-41D4-A716-446655440000")
        assert result == "550E8400-E29B-41D4-A716-446655440000"

    def test_invalid_uuid(self):
        with pytest.raises(ValueError, match="Invalid UUID"):
            validate_uuid("not-a-uuid")

    def test_empty_string(self):
        with pytest.raises(ValueError):
            validate_uuid("")

    def test_sql_injection_attempt(self):
        with pytest.raises(ValueError):
            validate_uuid("'; DROP TABLE projects; --")

    def test_partial_uuid(self):
        with pytest.raises(ValueError):
            validate_uuid("550e8400-e29b-41d4")


class TestValidateLimit:
    def test_valid_limit(self):
        assert validate_limit(20) == 20

    def test_boundary_low(self):
        assert validate_limit(1) == 1

    def test_boundary_high(self):
        assert validate_limit(200) == 200

    def test_zero_limit(self):
        with pytest.raises(ValueError):
            validate_limit(0)

    def test_negative_limit(self):
        with pytest.raises(ValueError):
            validate_limit(-5)

    def test_over_max(self):
        with pytest.raises(ValueError):
            validate_limit(201)

    def test_custom_max(self):
        assert validate_limit(50, max_val=100) == 50
        with pytest.raises(ValueError):
            validate_limit(101, max_val=100)


class TestValidateNoSecrets:
    def test_none_returns_none(self):
        assert validate_no_secrets(None) is None

    def test_normal_value_passes(self):
        assert validate_no_secrets("web-ui") == "web-ui"

    def test_email_passes(self):
        assert validate_no_secrets("user@example.com") == "user@example.com"

    def test_agent_key_rejected(self):
        with pytest.raises(ValueError, match="must not contain an API key"):
            validate_no_secrets("cm_sk_live_abc123xyz")

    def test_agent_key_in_message_rejected(self):
        with pytest.raises(ValueError, match="must not contain an API key"):
            validate_no_secrets("Hello, my key is cm_sk_live_abc123xyz please use it")

    def test_custom_field_name(self):
        with pytest.raises(ValueError, match="message must not contain"):
            validate_no_secrets("cm_sk_live_xxx", "message")
