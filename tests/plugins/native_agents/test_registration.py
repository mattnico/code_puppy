from code_puppy.callbacks import get_callbacks, get_feature_capability
from code_puppy.plugins.native_agents import config as config_module
from code_puppy.plugins.native_agents import register_callbacks as registration


def test_feature_is_off_by_default_and_does_not_initialize_storage(monkeypatch):
    monkeypatch.setattr(config_module, "get_value", lambda _key: None)
    monkeypatch.setattr(
        registration,
        "initialize_database",
        lambda _path: (_ for _ in ()).throw(AssertionError()),
    )
    registration._STORAGE_READY = False
    registration._initialize_if_enabled()
    assert registration.storage_ready() is False
    assert config_module.is_enabled() is False


def test_enabled_storage_failure_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        config_module,
        "get_value",
        lambda key: "true" if key == "native_agents_enabled" else None,
    )
    monkeypatch.setattr(
        registration, "database_path", lambda: tmp_path / "native.sqlite3"
    )
    monkeypatch.setattr(
        registration,
        "initialize_database",
        lambda _path: (_ for _ in ()).throw(OSError("locked")),
    )
    registration._STORAGE_READY = False
    registration._initialize_if_enabled()
    assert registration.storage_ready() is False


def test_registration_does_not_advertise_generic_tools():
    assert not any(
        callback.__module__.startswith("code_puppy.plugins.native_agents")
        for callback in get_callbacks("register_agent_tools")
    )
    assert get_feature_capability("unknown-native-feature") is False
