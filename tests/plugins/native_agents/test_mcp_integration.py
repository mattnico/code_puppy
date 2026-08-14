from code_puppy.plugins.native_agents.integrations import mcp


def test_mcp_integration_uses_existing_builder_path(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(mcp, "load_mcp_servers", lambda **kwargs: [sentinel])
    assert mcp.bound_servers("native-reviewer") == [sentinel]


def test_mcp_failure_is_optional_and_does_not_create_a_second_client(monkeypatch):
    monkeypatch.setattr(
        mcp,
        "load_mcp_servers",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    assert mcp.bound_servers("native-reviewer") == []
