from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from code_puppy.plugins.native_agents.contracts import (
    ExecutionIdentity,
    EventQuery,
    NativeEventKind,
    NativeStrategyName,
)
from code_puppy.plugins.native_agents.events import EventService, EventStore
from code_puppy.plugins.native_agents.state_store import StateStore


def _store(tmp_path):
    path = tmp_path / "native.sqlite3"
    state = StateStore(str(path))
    state.create_execution(
        ExecutionIdentity(
            execution_id="exec-query",
            agent_name="agent",
            method_name="method",
            created_at=datetime.now(timezone.utc),
        ),
        method_version=1,
        strategy=NativeStrategyName.PREDICT,
    )
    return EventStore(str(path))


def test_event_query_filters_kinds_and_omits_payload_by_default(tmp_path):
    store = _store(tmp_path)
    store.append("exec-query", NativeEventKind.EXECUTION_STARTED, {"secret": "x"})
    store.append("exec-query", NativeEventKind.VALIDATION_FAILED, {"error_code": "bad"})
    query = EventQuery(kinds=(NativeEventKind.VALIDATION_FAILED,), limit=2)
    events = store.query("exec-query", query)
    assert len(events) == 1
    assert events[0].payload == {}

    service = EventService("exec-query", store)
    assert service.query(query)[0].execution_id == "exec-query"
    summaries = service.summaries(query)
    assert summaries[0].summary == "validation failed: bad"
    assert summaries[0].occurred_at.tzinfo is not None


def test_event_query_rejects_unbounded_contracts():
    with pytest.raises(ValidationError):
        EventQuery(limit=0)
    with pytest.raises(ValidationError):
        EventQuery(limit=501)
    with pytest.raises(ValidationError):
        EventQuery(after_sequence=-1)
