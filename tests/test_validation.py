"""Tests for shared validation utilities."""
import pytest

from supabase_client import validate_uuid, validate_limit


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
