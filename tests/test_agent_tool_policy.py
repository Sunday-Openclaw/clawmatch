import agent_tool


def test_get_policy_passes_project_id_to_agent_gateway(monkeypatch):
    calls = []

    def fake_post_agent_api(agent_key, action, payload=None):
        calls.append((agent_key, action, payload))
        return {"ok": True}

    monkeypatch.setattr(agent_tool, "post_agent_api", fake_post_agent_api)

    result = agent_tool.get_policy("cm_sk_live_test", project_id="550e8400-e29b-41d4-a716-446655440000")

    assert result == {"ok": True}
    assert calls == [
        (
            "cm_sk_live_test",
            "get_policy",
            {"project_id": "550e8400-e29b-41d4-a716-446655440000"},
        )
    ]


def test_get_policy_without_project_id_uses_empty_payload(monkeypatch):
    calls = []

    def fake_post_agent_api(agent_key, action, payload=None):
        calls.append((agent_key, action, payload))
        return {"ok": True}

    monkeypatch.setattr(agent_tool, "post_agent_api", fake_post_agent_api)

    result = agent_tool.get_policy("cm_sk_live_test")

    assert result == {"ok": True}
    assert calls == [("cm_sk_live_test", "get_policy", {})]
