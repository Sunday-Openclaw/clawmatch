"""
Shared Supabase client configuration for Clawborate backend.

All Python files that need Supabase should import from here instead of
constructing their own URLs, keys, and headers.
"""

import os
import re
from typing import Any

import requests

# --- Configuration ---

SUPABASE_URL: str = os.environ.get("CLAWMATCH_SUPABASE_URL", "")
SUPABASE_ANON_KEY: str = os.environ.get("CLAWMATCH_SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY: str = os.environ.get("CLAWMATCH_SUPABASE_SERVICE_ROLE_KEY", "")

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def require_config() -> None:
    """Raise if required env vars are missing."""
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise RuntimeError("CLAWMATCH_SUPABASE_URL and CLAWMATCH_SUPABASE_ANON_KEY are required. " "See .env.example.")


def validate_uuid(value: str, name: str = "id") -> str:
    """Validate and return a UUID string, or raise ValueError."""
    if not value or not _UUID_RE.match(value):
        raise ValueError(f"Invalid UUID for {name}: {value!r}")
    return value


def validate_limit(limit: int, max_val: int = 200) -> int:
    """Validate limit is within acceptable range."""
    if limit < 1 or limit > max_val:
        raise ValueError(f"limit must be between 1 and {max_val}")
    return limit


_AGENT_KEY_PREFIX = "cm_sk_live_"


def validate_no_secrets(value: str | None, field_name: str = "field") -> str | None:
    """Reject values that contain an agent API key pattern."""
    if value is None:
        return None
    if _AGENT_KEY_PREFIX in value:
        raise ValueError(f"{field_name} must not contain an API key")
    return value


# --- Headers ---


def anon_headers(token: str) -> dict[str, str]:
    """Headers for requests authenticated with a user JWT."""
    return {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def rpc_headers() -> dict[str, str]:
    """Headers for RPC calls using anon key as bearer (gateway pattern)."""
    return {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
    }


def service_headers() -> dict[str, str]:
    """Headers using service role key. Raises if key is not configured."""
    if not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("CLAWMATCH_SUPABASE_SERVICE_ROLE_KEY is required for this operation")
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


# --- Standard API error ---


class SupabaseApiError(Exception):
    """Structured error for API responses."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message}


def error_dict(code: str, message: str) -> dict[str, Any]:
    """Standard error response envelope."""
    return {"error": code, "message": message}


# --- Common helpers ---


def get_current_user(token: str) -> dict[str, Any]:
    """Fetch the authenticated Supabase user."""
    hdrs = anon_headers(token)
    hdrs["Accept"] = "application/json"
    res = requests.get(f"{SUPABASE_URL}/auth/v1/user", headers=hdrs, timeout=30)
    res.raise_for_status()
    result: dict[str, Any] = res.json()
    return result


RPC_URL: str = f"{SUPABASE_URL}/rest/v1/rpc/agent_gateway" if SUPABASE_URL else ""
