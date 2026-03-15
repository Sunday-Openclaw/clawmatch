"""
Shared Supabase client configuration for Clawborate backend.

All Python files that need Supabase should import from here instead of
constructing their own URLs, keys, and headers.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict

import requests


_ENV_KEYS = {
    "CLAWMATCH_SUPABASE_URL",
    "CLAWMATCH_SUPABASE_ANON_KEY",
    "CLAWMATCH_SUPABASE_SERVICE_ROLE_KEY",
}


def _strip_env_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _load_dotenv_file(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False

    loaded_any = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key not in _ENV_KEYS or os.environ.get(key):
            continue
        os.environ[key] = _strip_env_value(value)
        loaded_any = True
    return loaded_any


def _load_local_dotenv() -> None:
    cwd_env = Path.cwd() / ".env"
    script_env = Path(__file__).resolve().parent.parent / ".env"

    checked = []
    for candidate in (cwd_env, script_env):
        if candidate in checked:
            continue
        checked.append(candidate)
        if _load_dotenv_file(candidate):
            break


_load_local_dotenv()

# --- Configuration ---

SUPABASE_URL: str = os.environ.get("CLAWMATCH_SUPABASE_URL", "")
SUPABASE_ANON_KEY: str = os.environ.get("CLAWMATCH_SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY: str = os.environ.get("CLAWMATCH_SUPABASE_SERVICE_ROLE_KEY", "")

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def require_config() -> None:
    """Raise if required config is missing after env/.env loading."""
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise RuntimeError(
            "CLAWMATCH_SUPABASE_URL and CLAWMATCH_SUPABASE_ANON_KEY are required. "
            "Set them in the environment or in a local .env file (see .env.example)."
        )


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


# --- Headers ---

def anon_headers(token: str) -> Dict[str, str]:
    """Headers for requests authenticated with a user JWT."""
    return {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def rpc_headers() -> Dict[str, str]:
    """Headers for RPC calls using anon key as bearer (gateway pattern)."""
    return {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
    }


def service_headers() -> Dict[str, str]:
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

    def to_dict(self) -> Dict[str, Any]:
        return {"error": self.code, "message": self.message}


def error_dict(code: str, message: str) -> Dict[str, Any]:
    """Standard error response envelope."""
    return {"error": code, "message": message}


# --- Common helpers ---

def get_current_user(token: str) -> Dict[str, Any]:
    """Fetch the authenticated Supabase user."""
    hdrs = anon_headers(token)
    hdrs["Accept"] = "application/json"
    res = requests.get(f"{SUPABASE_URL}/auth/v1/user", headers=hdrs, timeout=30)
    res.raise_for_status()
    return res.json()


RPC_URL: str = f"{SUPABASE_URL}/rest/v1/rpc/agent_gateway" if SUPABASE_URL else ""
