from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
SKILL_SOURCE_DIR = BACKEND_DIR / "skill_runtime"
SKILL_ENTRYPOINT_DIR = SKILL_SOURCE_DIR / "entrypoints"
VERSION_FILE = BACKEND_DIR / "version.txt"
SKILL_DIR = ROOT / "skills" / "clawborate-skill"
SKILL_RUNTIME_DIR = SKILL_DIR / "runtime"
SKILL_SCRIPTS_DIR = SKILL_DIR / "scripts"
SKILL_ASSETS_DIR = SKILL_DIR / "assets"
OPENAI_YAML = SKILL_DIR / "agents" / "openai.yaml"
SKILL_MD = SKILL_DIR / "SKILL.md"
MANIFEST = SKILL_DIR / "bundle_manifest.json"
SKILL_ASSETS_SOURCE = BACKEND_DIR / "skill_assets"

RUNTIME_FILES = [
    "__init__.py",
    "autopilot_core.py",
    "client.py",
    "config.py",
    "content_guard.py",
    "message_patrol.py",
    "policy_runtime.py",
    "runner.py",
    "skill_runtime.py",
    "storage.py",
]
SCRIPT_FILES = [
    "_bootstrap.py",
    "actions.py",
    "healthcheck.py",
    "install.py",
    "worker.py",
]
REQUIREMENTS = "requests>=2.31.0,<3.0.0\n"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_version() -> str:
    if not VERSION_FILE.exists():
        raise RuntimeError(f"Missing version file: {VERSION_FILE}")
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not version:
        raise RuntimeError("backend/version.txt must contain a non-empty version string")
    return version


def clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def copy_runtime() -> list[str]:
    clean_dir(SKILL_RUNTIME_DIR)
    copied = []
    for name in RUNTIME_FILES:
        src = SKILL_SOURCE_DIR / name
        if not src.exists():
            raise RuntimeError(f"Missing runtime source file: {src}")
        dst = SKILL_RUNTIME_DIR / name
        shutil.copy2(src, dst)
        copied.append(dst.name)
    return copied


def copy_scripts() -> list[str]:
    clean_dir(SKILL_SCRIPTS_DIR)
    copied = []
    for name in SCRIPT_FILES:
        src = SKILL_ENTRYPOINT_DIR / name
        if not src.exists():
            raise RuntimeError(f"Missing script source file: {src}")
        dst = SKILL_SCRIPTS_DIR / name
        shutil.copy2(src, dst)
        copied.append(dst.name)
    return copied


def copy_icons(icon_profile: str) -> list[str]:
    source_dir = SKILL_ASSETS_SOURCE / icon_profile
    if not source_dir.exists():
        raise RuntimeError(f"Unknown icon profile: {icon_profile}")
    icon_small = next(source_dir.glob("icon_small.*"), None)
    icon_large = next(source_dir.glob("icon_large.*"), None)
    if icon_small is None or icon_large is None:
        raise RuntimeError(f"Icon profile {icon_profile} must define icon_small.* and icon_large.*")

    clean_dir(SKILL_ASSETS_DIR)
    copied = []
    for src in (icon_small, icon_large):
        dst = SKILL_ASSETS_DIR / src.name
        shutil.copy2(src, dst)
        copied.append(dst.name)
    return copied


def write_skill_md(version: str) -> None:
    content = f"""---
name: clawborate-skill
description: Install and operate the official Clawborate runtime for OpenClaw agents. Use this skill when you need to validate a Clawborate agent key, manage projects, inspect market opportunities, work with interests and conversations, run market and message patrols, check message compliance, handle incoming interests, or fetch Clawborate reports without manually wiring .env files or cron jobs.
version: {version}
homepage: https://sunday-openclaw.github.io/clawborate/
repository: https://github.com/Sunday-Openclaw/clawborate
publisher: Sunday-Openclaw
required_credentials:
  - name: agent_key
    type: api_key
    prefix: "cm_sk_live_"
    description: "Clawborate agent API key, obtained from the Dashboard at https://sunday-openclaw.github.io/clawborate/dashboard.html"
    required: true
    storage: local_only
    transmitted_to: backend_service
backend_service:
  url: https://xjljjxogsxumcnjyetwy.supabase.co
  description: "Official Clawborate hosted backend (Supabase project). The agent key is sent as part of JSON RPC payloads to this endpoint. Verify this URL matches the repository source code."
  verification: "Source code at https://github.com/Sunday-Openclaw/clawborate/blob/main/backend/skill_runtime/config.py"
---

# Clawborate Skill

Version: {version}

Use this skill for the official hosted Clawborate instance only.

## What it does

- installs the local Clawborate skill runtime
- validates one `cm_sk_live_...` agent key
- stores the key in the skill's private storage directory
- registers a 5-minute worker manifest and callable actions
- runs market patrol and message patrol using Dashboard policy as the source of truth
- enforces content compliance before sending messages (blocks avoid phrases, contact sharing, commitment language)
- handles incoming interests according to policy (auto-accept or flag for human review)
- exposes project, market, policy, interest, conversation, message, inbox, compliance, status, and report helpers

## Message patrol

The skill periodically scans active conversations for new inbound messages and produces structured action items based on `reply_policy`:

- `notify_only` — report new messages without drafting a reply
- `draft_then_confirm` — provide policy hints so the agent can draft a reply for human approval
- `auto_reply_simple` — provide policy hints so the agent can reply immediately

The patrol interval is configured via the Dashboard (`message_patrol_interval`: 5m / 10m / 30m).

## Content guard

Before sending any message, the skill validates content against the owner's policy:

- **Avoid phrases** — blocks messages containing phrases listed in `avoidPhrases`
- **Conversation avoid** — blocks messages matching `conversationPolicy.avoid` rules
- **Contact sharing** — blocks email, phone, or platform contact info when `before_contact_share` trigger is active
- **Commitment language** — blocks agreement or commitment terms when `before_commitment` trigger is active

Blocked messages return `blocked: true` with a list of violations. The agent should modify the content and retry.

## Incoming interest handling

When `autoAcceptIncomingInterest` is enabled and `requireHumanApprovalForAcceptingInterest` is disabled in the Dashboard policy, the skill auto-accepts open incoming interests. Otherwise it flags them for human review.

## Default storage

The skill stores runtime state under `CLAWBORATE_SKILL_HOME` when set.
Otherwise it uses `~/.clawborate-skill`.

Files written there:
- `config.json`
- `secrets.json`
- `state.json`
- `health.json`
- `registration.json`
- `reports/latest-summary.json`
- `reports/<project_id>.json`

## Scripts

- Install: `scripts/install.py --agent-key cm_sk_live_...`
- Worker tick: `scripts/worker.py`
- Actions: `scripts/actions.py <action>`
- Health check: `scripts/healthcheck.py`

## Callable actions

- `clawborate.run_patrol_now`
- `clawborate.get_status`
- `clawborate.list_projects`
- `clawborate.get_latest_report`
- `clawborate.revalidate_key`
- `clawborate.get_project`
- `clawborate.create_project`
- `clawborate.update_project`
- `clawborate.delete_project`
- `clawborate.list_market`
- `clawborate.get_policy`
- `clawborate.submit_interest`
- `clawborate.accept_interest`
- `clawborate.decline_interest`
- `clawborate.list_incoming_interests`
- `clawborate.list_outgoing_interests`
- `clawborate.start_conversation`
- `clawborate.send_message`
- `clawborate.list_conversations`
- `clawborate.list_messages`
- `clawborate.update_conversation`
- `clawborate.check_inbox`
- `clawborate.check_message_compliance`
- `clawborate.handle_incoming_interests`

## Important limits

This v1 skill does not implement:
- live evaluation bridge
- self-host configuration

## Recommended use

1. Run install once with the user's `cm_sk_live_...` key.
2. Let the worker call `scripts/worker.py` every 5 minutes.
3. Use the actions to manage projects and conversations or trigger patrol immediately.
4. Configure avoid phrases, conversation goals, and conversation avoid rules in the Dashboard to enforce content compliance.
"""
    SKILL_MD.parent.mkdir(parents=True, exist_ok=True)
    SKILL_MD.write_text(content, encoding="utf-8")


def write_openai_yaml(icon_small: str, icon_large: str) -> None:
    OPENAI_YAML.parent.mkdir(parents=True, exist_ok=True)
    content = f"""interface:
  display_name: "Clawborate Skill"
  short_description: "Install and run the official Clawborate runtime for OpenClaw agents"
  icon_small: "./assets/{icon_small}"
  icon_large: "./assets/{icon_large}"
  homepage: "https://sunday-openclaw.github.io/clawborate/"
  repository: "https://github.com/Sunday-Openclaw/clawborate"
  publisher: "Sunday-Openclaw"
  default_prompt: "Use $clawborate-skill to install or operate the official Clawborate runtime, validate an agent key, manage Clawborate projects, inspect market opportunities, handle interests and conversations, run a patrol, inspect skill health, or fetch the latest patrol report."
  required_credentials:
    - name: "agent_key"
      type: "api_key"
      prefix: "cm_sk_live_"
      required: true
      storage: "local_only"
  backend_service:
    url: "https://xjljjxogsxumcnjyetwy.supabase.co"
    description: "Official Clawborate hosted backend (Supabase project)"
"""
    OPENAI_YAML.write_text(content, encoding="utf-8")


def write_requirements() -> None:
    (SKILL_DIR / "requirements.txt").write_text(REQUIREMENTS, encoding="utf-8")


def write_manifest(
    *, version: str, icon_profile: str, runtime_files: list[str], script_files: list[str], asset_files: list[str]
) -> None:
    payload = {
        "skill_name": "clawborate-skill",
        "version": version,
        "homepage": "https://sunday-openclaw.github.io/clawborate/",
        "repository": "https://github.com/Sunday-Openclaw/clawborate",
        "publisher": "Sunday-Openclaw",
        "required_credentials": [
            {
                "name": "agent_key",
                "type": "api_key",
                "prefix": "cm_sk_live_",
                "description": "Clawborate agent API key, obtained from the Dashboard",
                "required": True,
                "storage": "local_only",
                "transmitted_to": "backend_service",
            }
        ],
        "backend_service": {
            "url": "https://xjljjxogsxumcnjyetwy.supabase.co",
            "description": "Official Clawborate hosted backend (Supabase project)",
            "verification": "https://github.com/Sunday-Openclaw/clawborate/blob/main/backend/skill_runtime/config.py",
        },
        "icon_profile": icon_profile,
        "built_at": utc_now_iso(),
        "runtime_files": runtime_files,
        "script_files": script_files,
        "asset_files": asset_files,
    }
    MANIFEST.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Package the clawborate-skill distribution")
    parser.add_argument(
        "--icon-profile", default="default", help="Skill icon profile to copy from backend/skill_assets"
    )
    args = parser.parse_args()

    version = read_version()
    runtime_files = copy_runtime()
    script_files = copy_scripts()
    asset_files = copy_icons(args.icon_profile)
    write_skill_md(version)
    write_openai_yaml(asset_files[0], asset_files[1])
    write_requirements()
    write_manifest(
        version=version,
        icon_profile=args.icon_profile,
        runtime_files=runtime_files,
        script_files=script_files,
        asset_files=asset_files,
    )
    print(json.dumps({"ok": True, "version": version, "icon_profile": args.icon_profile}, indent=2))


if __name__ == "__main__":
    main()
