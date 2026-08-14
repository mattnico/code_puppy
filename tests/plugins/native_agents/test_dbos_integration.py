from code_puppy.plugins.native_agents.integrations import dbos


def test_dbos_absent_or_not_launched_is_safe(monkeypatch):
    monkeypatch.setattr(dbos, "status", lambda: {"available": False, "launched": False})
    assert dbos.can_wrap_predict() is False


def test_dbos_wrapper_is_only_reported_when_existing_lifecycle_is_live(monkeypatch):
    monkeypatch.setattr(
        dbos, "status", lambda: {"available": True, "enabled": True, "launched": True}
    )
    assert dbos.can_wrap_predict() is True
