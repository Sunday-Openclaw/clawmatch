"""
Common evaluation result schema for all Clawborate matchers/evaluators.
"""
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional


@dataclass
class EvaluationResult:
    score: int                # 0-100
    confidence: float         # 0.0-1.0
    reason: str
    should_connect: bool
    best_project_name: Optional[str] = None
    source: str = "unknown"   # "matcher", "autopilot", "openclaw"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvaluationResult":
        return cls(
            score=int(data.get("score", 0)),
            confidence=float(data.get("confidence", 0.0)),
            reason=str(data.get("reason", "")),
            should_connect=bool(data.get("should_connect", False)),
            best_project_name=data.get("best_project_name"),
            source=data.get("source", "unknown"),
        )
