import pytest

from code_puppy.plugins.native_agents.integrations import permissions


def test_file_permission_adapter_denies_explicit_false(monkeypatch):
    monkeypatch.setattr(permissions, "on_file_permission", lambda *args: [None, False])
    assert permissions.file_allowed("ctx", "file.txt", "read") is False


def test_file_permission_adapter_allows_no_opinion(monkeypatch):
    monkeypatch.setattr(permissions, "on_file_permission", lambda *args: [None, True])
    assert permissions.file_allowed("ctx", "file.txt", "read") is True


@pytest.mark.asyncio
async def test_tool_observation_uses_pre_tool_hook(monkeypatch):
    async def callback(*args):
        return ["observed"]

    monkeypatch.setattr(permissions, "on_pre_tool_call", callback)
    assert await permissions.observe_tool_call("read_file", {"path": "x"}) == [
        "observed"
    ]
