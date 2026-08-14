from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, ConfigDict

from code_puppy.plugins.native_agents.contracts import (
    NativeEventKind,
    NativeExecutionStatus,
)
from code_puppy.plugins.native_agents.demo_agent import (
    ChangeSummaryInput,
    ChangeSummaryResult,
    NativeReviewerAgent,
)
from code_puppy.plugins.native_agents.errors import (
    EventStoreError,
    NativeOutputValidationError,
    NativeStorageUnavailableError,
    StateSchemaError,
)
from code_puppy.plugins.native_agents.events import EventStore
from code_puppy.plugins.native_agents.runtime import NativeMethodRuntime
from code_puppy.plugins.native_agents.state import StateService
from code_puppy.plugins.native_agents.state_store import StateStore


class InvalidState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: str


class ReturningStrategy:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def execute(self, *args, **kwargs):
        self.calls += 1
        return self.result


class FailingStrategy:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    async def execute(self, *args, **kwargs):
        self.calls += 1
        raise self.error


def _input():
    return ChangeSummaryInput(request="review", diff_text="+ value")


def _result():
    return ChangeSummaryResult(
        summary="summary",
        findings=[],
        confidence="high",
        limitations=[],
    )


@pytest.mark.asyncio
async def test_runtime_returns_typed_result_and_records_finished_events(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "code_puppy.plugins.native_agents.runtime.is_enabled", lambda: True
    )
    strategy = ReturningStrategy(_result())
    runtime = NativeMethodRuntime(
        db_path=tmp_path / "native.sqlite3", predict_strategy=strategy
    )
    agent = NativeReviewerAgent()
    result = await runtime.execute(
        agent, agent.get_native_method("summarize_change"), _input()
    )
    assert isinstance(result, ChangeSummaryResult)
    assert strategy.calls == 1
    execution_id = _find_execution_id(tmp_path)
    events = EventStore(str(tmp_path / "native.sqlite3")).list_events(
        execution_id, limit=10
    )
    assert [event.kind for event in events] == [
        NativeEventKind.EXECUTION_STARTED,
        NativeEventKind.CONTEXT_RENDERED,
        NativeEventKind.EXECUTION_FINISHED,
    ]


def _find_execution_id(tmp_path):
    import sqlite3

    connection = sqlite3.connect(tmp_path / "native.sqlite3")
    try:
        return connection.execute(
            "SELECT execution_id FROM native_executions"
        ).fetchone()[0]
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_validation_failure_is_bounded_evented_and_typed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "code_puppy.plugins.native_agents.runtime.is_enabled", lambda: True
    )
    strategy = FailingStrategy(NativeOutputValidationError("bad output"))
    runtime = NativeMethodRuntime(
        db_path=tmp_path / "native.sqlite3", predict_strategy=strategy
    )
    agent = NativeReviewerAgent()
    with pytest.raises(NativeOutputValidationError):
        await runtime.execute(
            agent, agent.get_native_method("summarize_change"), _input()
        )

    execution_id = _find_execution_id(tmp_path)
    events = EventStore(str(tmp_path / "native.sqlite3")).list_events(
        execution_id, limit=10
    )
    assert [event.kind for event in events] == [
        NativeEventKind.EXECUTION_STARTED,
        NativeEventKind.CONTEXT_RENDERED,
        NativeEventKind.VALIDATION_FAILED,
        NativeEventKind.EXECUTION_FAILED,
    ]
    record = StateStore(
        str(tmp_path / "native.sqlite3"), initialize=False
    ).get_execution(execution_id)
    assert record.status is NativeExecutionStatus.FAILED
    assert strategy.calls == 1


@pytest.mark.asyncio
async def test_setup_failure_marks_created_execution_terminal(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "code_puppy.plugins.native_agents.runtime.is_enabled", lambda: True
    )

    class FailingStartStore(EventStore):
        def append(self, execution_id, kind, payload):
            if kind is NativeEventKind.EXECUTION_STARTED:
                raise EventStoreError("locked")
            return super().append(execution_id, kind, payload)

    monkeypatch.setattr(
        "code_puppy.plugins.native_agents.runtime.EventStore", FailingStartStore
    )
    strategy = ReturningStrategy(_result())
    runtime = NativeMethodRuntime(
        db_path=tmp_path / "native.sqlite3", predict_strategy=strategy
    )
    agent = NativeReviewerAgent()
    with pytest.raises(EventStoreError):
        await runtime.execute(
            agent,
            agent.get_native_method("summarize_change"),
            _input(),
        )
    record = StateStore(
        str(tmp_path / "native.sqlite3"), initialize=False
    ).get_execution(_find_execution_id(tmp_path))
    assert record.status is NativeExecutionStatus.FAILED
    assert strategy.calls == 0


@pytest.mark.asyncio
async def test_semantically_corrupt_state_fails_before_strategy(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "code_puppy.plugins.native_agents.runtime.is_enabled", lambda: True
    )
    monkeypatch.setattr(
        StateService,
        "get",
        lambda _self: (_ for _ in ()).throw(
            StateSchemaError("stored state no longer matches its schema")
        ),
    )
    agent = NativeReviewerAgent()
    spec = agent.get_native_method("summarize_change").model_copy(
        update={
            "state_type": InvalidState,
            "state_schema_name": "InvalidState",
            "state_factory": lambda _payload: InvalidState(status="ready"),
        }
    )
    strategy = ReturningStrategy(_result())
    runtime = NativeMethodRuntime(
        db_path=tmp_path / "native.sqlite3", predict_strategy=strategy
    )
    with pytest.raises(StateSchemaError):
        await runtime.execute(agent, spec, _input())
    assert strategy.calls == 0


@pytest.mark.asyncio
async def test_storage_failure_happens_before_strategy(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "code_puppy.plugins.native_agents.runtime.is_enabled", lambda: True
    )
    monkeypatch.setattr(
        "code_puppy.plugins.native_agents.runtime.initialize_database",
        lambda _path: (_ for _ in ()).throw(NativeStorageUnavailableError("locked")),
    )
    strategy = ReturningStrategy(_result())
    runtime = NativeMethodRuntime(
        db_path=tmp_path / "native.sqlite3", predict_strategy=strategy
    )
    agent = NativeReviewerAgent()
    with pytest.raises(NativeStorageUnavailableError):
        await runtime.execute(
            agent, agent.get_native_method("summarize_change"), _input()
        )
    assert strategy.calls == 0


@pytest.mark.asyncio
async def test_cancellation_is_not_marked_success(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "code_puppy.plugins.native_agents.runtime.is_enabled", lambda: True
    )
    strategy = FailingStrategy(asyncio.CancelledError())
    runtime = NativeMethodRuntime(
        db_path=tmp_path / "native.sqlite3", predict_strategy=strategy
    )
    agent = NativeReviewerAgent()
    with pytest.raises(asyncio.CancelledError):
        await runtime.execute(
            agent, agent.get_native_method("summarize_change"), _input()
        )
    record = StateStore(
        str(tmp_path / "native.sqlite3"), initialize=False
    ).get_execution(_find_execution_id(tmp_path))
    assert record.status is NativeExecutionStatus.CANCELLED


@pytest.mark.asyncio
async def test_runtime_revalidates_injected_strategy_output(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "code_puppy.plugins.native_agents.runtime.is_enabled", lambda: True
    )
    strategy = ReturningStrategy({"summary": "not a model"})
    runtime = NativeMethodRuntime(
        db_path=tmp_path / "native.sqlite3", predict_strategy=strategy
    )
    agent = NativeReviewerAgent()
    with pytest.raises(NativeOutputValidationError):
        await runtime.execute(
            agent, agent.get_native_method("summarize_change"), _input()
        )
    execution_id = _find_execution_id(tmp_path)
    events = EventStore(str(tmp_path / "native.sqlite3")).list_events(
        execution_id, limit=10
    )
    assert events[-1].kind is NativeEventKind.EXECUTION_FAILED
