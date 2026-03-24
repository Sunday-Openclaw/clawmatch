from __future__ import annotations

import argparse
import json
from pathlib import Path

from _bootstrap import SKILL_ROOT  # noqa: F401
from runtime.client import AgentGatewayError, AgentGatewayTransportError
from runtime.skill_runtime import InstallError, run_worker_tick


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one Clawborate skill worker tick")
    parser.add_argument("--skill-home", help="Override skill storage directory")
    args = parser.parse_args()

    home = Path(args.skill_home).expanduser() if args.skill_home else None
    try:
        result = run_worker_tick(home=home)
    except (InstallError, AgentGatewayError, AgentGatewayTransportError) as exc:
        payload = exc.to_dict() if hasattr(exc, "to_dict") else {"error": exc.__class__.__name__, "message": str(exc)}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        raise SystemExit(1) from exc

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
