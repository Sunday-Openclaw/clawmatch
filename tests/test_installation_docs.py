from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_install_md_documents_periodic_cron_setup() -> None:
    content = (ROOT / "INSTALL.md").read_text(encoding="utf-8")

    assert "## Set Up the Periodic Cron" in content
    assert "bootstrap-plan.json" in content
    assert "scripts/actions.py get-bootstrap-plan" in content
    assert "openclaw cron list --json" in content
    assert "openclaw cron edit JOB_ID" in content
    assert "openclaw cron add" in content
    assert "clawborate-patrol" in content
    assert 'periodic_worker: "host_support_needed"' in content


def test_skill_md_mentions_bootstrap_plan_and_cron() -> None:
    content = (ROOT / "skills" / "clawborate-skill" / "SKILL.md").read_text(encoding="utf-8")

    assert "bootstrap-plan.json" in content
    assert "OpenClaw cron" in content
    assert "clawborate-patrol" in content
    assert "host_support_needed" in content
