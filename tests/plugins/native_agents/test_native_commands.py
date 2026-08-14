from code_puppy.plugins.native_agents import commands


def test_native_commands_are_feature_gated_and_namespaced(monkeypatch):
    monkeypatch.setattr(commands, "is_enabled", lambda: False)
    assert commands.handle_native_command("/native status", "native") == commands.t(
        "native_agents.command.disabled"
    )
    assert commands.handle_native_command("/other status", "other") is None
    assert commands.native_command_help() == []


def test_cleanup_command_is_feature_and_diagnostics_gated(monkeypatch):
    monkeypatch.setattr(commands, "is_enabled", lambda: True)
    monkeypatch.setattr(commands, "diagnostics_enabled", lambda: True)
    monkeypatch.setattr(commands, "store_retention_days", lambda: 30)
    monkeypatch.setattr(
        commands,
        "StateStore",
        lambda *args, **kwargs: type(
            "Store", (), {"purge_expired": lambda self, _days: 2}
        )(),
    )
    output = commands.handle_native_command("/native cleanup", "native")
    assert "2" in output
    monkeypatch.setattr(commands, "is_enabled", lambda: True)
    monkeypatch.setattr(commands, "diagnostics_enabled", lambda: True)
    monkeypatch.setattr(commands, "codeact_enabled", lambda: False)
    monkeypatch.setattr(commands, "schema_version", lambda _path: 1)
    monkeypatch.setattr(commands, "LATEST_SCHEMA_VERSION", 1)
    monkeypatch.setattr(commands, "database_path", lambda: "/not-shown-in-output")
    monkeypatch.setattr(
        commands, "dbos", type("DBOS", (), {"status": staticmethod(lambda: {})})
    )
    output = commands.handle_native_command("/native diagnostics", "native")
    assert "Native diagnostics" in output
    assert "/not-shown" not in output
