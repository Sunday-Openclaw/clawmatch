from __future__ import annotations

import sys
from pathlib import Path

# Force UTF-8 stdout/stderr on Windows where the default encoding is often
# GBK (cp936).  Without this, json.dumps(ensure_ascii=False) produces
# GBK-encoded bytes that downstream consumers (OpenClaw, agent runtimes)
# misinterpret as UTF-8, garbling all non-ASCII text.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))
