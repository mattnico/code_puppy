from datetime import datetime, timezone

import pytest
from pydantic import BaseModel, ConfigDict, Field

from code_puppy.plugins.native_agents.contracts import (
    ExecutionIdentity,
    NativeExecutionStatus,
    NativeStrategyName,
)
from code_puppy.plugins.native_agents.errors import StateConflictError, StateSchemaError
from code_puppy.plugins.native_agents.state_store import StateStore
from code_puppy.plugins.native_agents.storage import (
    LATEST_SCHEMA_VERSION,
    schema_version,
)


class State(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    count: int
    token: str = Field(json_schema_extra={"secret": True})


class LooseState(BaseModel):
    count: int


def _identity(execution_id: str = "exec-1") -> ExecutionIdentity:
    return ExecutionIdentity(
        execution_id=execution_id,
        agent_name="test-agent",
        method_name="review",
        created_at=datetime.now(timezone.utc),
    )


def test_fresh_database_migrates_idempotently_and_survives_reopen(tmp_path):
    path = tmp_path / "native.sqlite3"
    store = StateStore(str(path))
    assert schema_version(path) == LATEST_SCHEMA_VERSION
    store_again = StateStore(str(path))
    assert schema_version(path) == LATEST_SCHEMA_VERSION

    record = store.create_execution(
        _identity(), method_version=1, strategy=NativeStrategyName.PREDICT
    )
    assert store_again.get_execution(record.execution_id) == record


def test_state_initialization_and_update_are_revisioned_and_evented(tmp_path):
    store = StateStore(str(tmp_path / "native.sqlite3"))
    execution = store.create_execution(
        _identity(), method_version=1, strategy=NativeStrategyName.PREDICT
    )

    initial = store.initialize_state(
        execution.execution_id,
        State(count=1, token="secret"),
        schema_name="State",
        schema_version=1,
    )
    assert initial.revision == 1
    assert initial.state_json == {"count": 1, "token": "[REDACTED]"}

    updated = store.update_state(
        execution.execution_id,
        expected_revision=1,
        state=State(count=2, token="new-secret"),
        schema_name="State",
        schema_version=1,
    )
    assert updated.revision == 2
    assert updated.state_json["token"] == "[REDACTED]"
    assert store.get_state(execution.execution_id).revision == 2

    with pytest.raises(StateConflictError):
        store.update_state(
            execution.execution_id,
            expected_revision=1,
            state=State(count=3, token="secret"),
            schema_name="State",
            schema_version=1,
        )
    assert store.get_state(execution.execution_id).state_json["count"] == 2


def test_execution_status_is_persisted(tmp_path):
    store = StateStore(str(tmp_path / "native.sqlite3"))
    record = store.create_execution(
        _identity(), method_version=1, strategy=NativeStrategyName.PREDICT
    )
    running = store.set_execution_status(
        record.execution_id, NativeExecutionStatus.RUNNING
    )
    assert running.status is NativeExecutionStatus.RUNNING
    finished = store.set_execution_status(
        record.execution_id,
        NativeExecutionStatus.FAILED,
        error_code="validation_failed",
        error_summary="bounded summary",
    )
    assert finished.status is NativeExecutionStatus.FAILED
    assert finished.error_code == "validation_failed"
    assert finished.finished_at is not None


def test_corrupt_persisted_state_is_a_typed_failure(tmp_path):
    import sqlite3

    path = tmp_path / "native.sqlite3"
    store = StateStore(str(path))
    record = store.create_execution(
        _identity(), method_version=1, strategy=NativeStrategyName.PREDICT
    )
    store.initialize_state(
        record.execution_id,
        State(count=1, token="secret"),
        schema_name="State",
        schema_version=1,
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE native_state_snapshots SET state_json = ?",
            ("{malformed",),
        )
    with pytest.raises(StateSchemaError):
        store.get_state(record.execution_id)


def test_state_persistence_rejects_non_strict_models(tmp_path):
    store = StateStore(str(tmp_path / "native.sqlite3"))
    record = store.create_execution(
        _identity(), method_version=1, strategy=NativeStrategyName.PREDICT
    )
    with pytest.raises(StateSchemaError):
        store.initialize_state(
            record.execution_id,
            LooseState(count=1),
            schema_name="LooseState",
            schema_version=1,
        )


def test_state_requires_pydantic_json_object(tmp_path):
    store = StateStore(str(tmp_path / "native.sqlite3"))
    record = store.create_execution(
        _identity(), method_version=1, strategy=NativeStrategyName.PREDICT
    )
    with pytest.raises(StateSchemaError):
        store.initialize_state(
            record.execution_id,
            {"count": 1},  # type: ignore[arg-type]
            schema_name="State",
            schema_version=1,
        )
