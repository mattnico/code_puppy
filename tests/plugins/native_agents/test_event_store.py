from datetime import datetime, timezone

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from code_puppy.plugins.native_agents.contracts import (
    ExecutionIdentity,
    NativeEventKind,
    NativeStrategyName,
)
from code_puppy.plugins.native_agents.errors import EventStoreError
from code_puppy.plugins.native_agents.events import EventStore, redact_payload
from code_puppy.plugins.native_agents.state_store import StateStore


def _store(tmp_path):
    path = tmp_path / "native.sqlite3"
    state_store = StateStore(str(path))
    state_store.create_execution(
        ExecutionIdentity(
            execution_id="exec-1",
            agent_name="agent",
            method_name="method",
            created_at=datetime.now(timezone.utc),
        ),
        method_version=1,
        strategy=NativeStrategyName.PREDICT,
    )
    return EventStore(str(path))


def test_event_store_orders_and_bounds_immutable_events(tmp_path):
    store = _store(tmp_path)
    first = store.append("exec-1", NativeEventKind.EXECUTION_STARTED, {"step": 1})
    second = store.append("exec-1", NativeEventKind.CONTEXT_RENDERED, {"step": 2})
    assert [event.sequence for event in store.list_events("exec-1", limit=10)] == [1, 2]
    assert store.list_events("exec-1", limit=10, after_sequence=1) == [second]
    with pytest.raises(ValidationError):
        first.sequence = 10
    assert first.sequence == 1


def test_event_payload_is_redacted_and_json_safe(tmp_path):
    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        token: str = Field(json_schema_extra={"secret": True})
        message: str

    payload = Payload(token="top-secret", message="Bearer abcdefghijkl")
    assert redact_payload(payload) == {
        "token": "[REDACTED]",
        "message": "[REDACTED]",
    }
    store = _store(tmp_path)
    event = store.append("exec-1", NativeEventKind.EXECUTION_FAILED, payload)
    assert event.redacted is True
    assert event.payload["token"] == "[REDACTED]"
    assert "top-secret" not in event.model_dump_json()


def test_event_query_requires_explicit_bounded_limit(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(EventStoreError):
        store.list_events("exec-1", limit=0)
    with pytest.raises(EventStoreError):
        store.list_events("exec-1", limit=501)
    with pytest.raises(EventStoreError):
        store.list_events("exec-1", limit=1, after_sequence=-1)


def test_redaction_rejects_non_mapping_and_naive_timestamps():
    with pytest.raises(EventStoreError):
        redact_payload(["not", "a", "mapping"])

    from code_puppy.plugins.native_agents.storage import timestamp

    with pytest.raises(ValueError):
        timestamp(datetime.now())
