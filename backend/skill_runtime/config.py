from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

OFFICIAL_BASE_URL = "https://xjljjxogsxumcnjyetwy.supabase.co"
OFFICIAL_ANON_KEY = "sb_publishable_dlgv32Zav_IaU_l6LVYu0A_CIz-Ww_u"
DEFAULT_WORKER_TICK_SECONDS = 300


@dataclass(frozen=True)
class ClawborateConfig:
    base_url: str = OFFICIAL_BASE_URL
    anon_key: str = OFFICIAL_ANON_KEY
    worker_tick_seconds: int = DEFAULT_WORKER_TICK_SECONDS
    agent_contact: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ClawborateConfig:
        payload = data or {}
        return cls(
            base_url=str(payload.get("base_url") or OFFICIAL_BASE_URL),
            anon_key=str(payload.get("anon_key") or OFFICIAL_ANON_KEY),
            worker_tick_seconds=int(payload.get("worker_tick_seconds") or DEFAULT_WORKER_TICK_SECONDS),
            agent_contact=payload.get("agent_contact"),
        )
