from datetime import datetime, timezone

import pytest
from pydantic import BaseModel, ConfigDict, Field

from code_puppy.plugins.native_agents.contracts import (
    ExecutionIdentity,
    NativeEventKind,
    NativeStrategyName,
)
from code_puppy.plugins.native_agents.errors import StateConflictError, StateSchemaError
from code_puppy.plugins.native_agents.events import EventStore
from code_puppy.plugins.native_agents.state import StateService
from code_puppy.plugins.native_agents.state_store import StateStore


class ReviewState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    __native_context_fields__ = ("status", "count")
    status: str
    count: int
    token: str = Field(json_schema_extra={"secret": True})


def _service(tmp_path):
    path = tmp_path / "native.sqlite3"
    store = StateStore(str(path))
    identity = ExecutionIdentity(
        execution_id="exec-state",
        agent_name="agent",
        method_name="review",
        created_at=datetime.now(timezone.utc),
    )
    store.create_execution(
        identity, method_version=1, strategy=NativeStrategyName.PREDICT
    )
    return StateService(
        execution_id=identity.execution_id,
        state_type=ReviewState,
        state_store=store,
        event_store=EventStore(str(path)),
        max_bytes=1024,
    )


def test_state_service_requires_identifier_reason_and_records_changed_fields(tmp_path):
    service = _service(tmp_path)
    initial = service.initialize(ReviewState(status="running", count=1, token="secret"))
    updated = service.replace(
        ReviewState(status="passed", count=2, token="new-secret"),
        expected_revision=initial.revision,
        reason="verification.complete",
    )
    assert updated.revision == 2
    events = service.event_store.list_events("exec-state", limit=10)
    state_event = next(
        event for event in events if event.kind is NativeEventKind.STATE_UPDATED
    )
    assert state_event.payload["changed_fields"] == ["count", "status", "token"]
    assert state_event.payload["reason"] == "verification.complete"
    assert "secret" not in state_event.model_dump_json()

    with pytest.raises(StateSchemaError):
        service.replace(
            ReviewState(status="failed", count=3, token="secret"),
            expected_revision=2,
            reason="not a stable reason",
        )


def test_state_service_rejects_stale_revision_and_oversize_state(tmp_path):
    service = _service(tmp_path)
    service.initialize(ReviewState(status="running", count=1, token="x"))
    with pytest.raises(StateSchemaError):
        service.replace(
            ReviewState(status="passed", count=2, token="x"),
            expected_revision=0,
            reason="verification.complete",
        )
    with pytest.raises(StateConflictError):
        service.replace(
            ReviewState(status="passed", count=2, token="x"),
            expected_revision=999,
            reason="verification.complete",
        )

    tiny = _service(tmp_path / "tiny")
    with pytest.raises(StateSchemaError):
        tiny.initialize(ReviewState(status="x" * 2000, count=1, token="x"))


def test_state_service_reads_current_typed_state(tmp_path):
    service = _service(tmp_path)
    service.initialize(ReviewState(status="running", count=1, token="secret"))
    loaded = service.get()
    assert isinstance(loaded, ReviewState)
    assert loaded.status == "running"
    assert loaded.token == "[REDACTED]"
