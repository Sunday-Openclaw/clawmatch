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
