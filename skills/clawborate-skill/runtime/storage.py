from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_HEALTH = {
    "status": "not_installed",
    "paused": False,
    "paused_reason": None,
    "last_attempt_at": None,
    "last_success_at": None,
    "last_error": None,
    "consecutive_failures": 0,
}

DEFAULT_COUNTERS: dict[str, int] = {
    "I": 0,
    "M": 0,
    "R": 0,
    "T": 0,
}

DEFAULT_STATE = {
    "schema_version": 2,
    "tick_id": None,
    "projects": {},
    "conversations": {},
    "pending_actions": {},
    "counters": DEFAULT_COUNTERS.copy(),
    "incoming_interest_notifications": {},
    "bootstrap": {},
}


@dataclass(frozen=True)
class StorageLayout:
    root: Path
    config_path: Path
    secrets_path: Path
    state_path: Path
    health_path: Path
    reports_dir: Path
    latest_report_path: Path
    registration_path: Path

    @classmethod
    def from_root(cls, root: Path) -> StorageLayout:
        reports_dir = root / "reports"
        return cls(
            root=root,
            config_path=root / "config.json",
            secrets_path=root / "secrets.json",
            state_path=root / "state.json",
            health_path=root / "health.json",
            reports_dir=reports_dir,
            latest_report_path=reports_dir / "latest-summary.json",
            registration_path=root / "registration.json",
        )

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_health(path: Path) -> dict[str, Any]:
    health = dict(DEFAULT_HEALTH)
    health.update(load_json(path, {}))
    return health


def write_health(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    health = dict(DEFAULT_HEALTH)
    health.update(payload)
    save_json(path, health)
    return health


def _coerce_counters(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}

    counters: dict[str, int] = {}
    for key, raw_value in value.items():
        if isinstance(raw_value, int):
            counters[str(key)] = raw_value
    return counters


def load_state(path: Path) -> dict[str, Any]:
    state = load_json(path, {})
    merged = dict(DEFAULT_STATE)
    merged.update(state)
    default_counters = dict(DEFAULT_COUNTERS)
    merged["counters"] = {
        **default_counters,
        **_coerce_counters(state.get("counters")),
    }
    return merged


def write_state(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    state = dict(DEFAULT_STATE)
    state.update(payload)
    default_counters = dict(DEFAULT_COUNTERS)
    state["counters"] = {
        **default_counters,
        **_coerce_counters(payload.get("counters")),
    }
    save_json(path, state)
    return state
