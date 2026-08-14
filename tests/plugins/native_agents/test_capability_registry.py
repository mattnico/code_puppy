from datetime import datetime, timezone

import pytest
from pydantic import BaseModel, ConfigDict, Field

from code_puppy.plugins.native_agents.capabilities import CapabilityRegistry
from code_puppy.plugins.native_agents.contracts import (
    CapabilityEffect,
    CapabilitySpec,
    ExecutionIdentity,
    MethodSpec,
    NativeEventKind,
    NativeStrategyName,
    ReferencePreview,
)
from code_puppy.plugins.native_agents.errors import (
    CapabilityDeniedError,
    CapabilityValidationError,
    HandleUnavailableError,
)
from code_puppy.plugins.native_agents.events import EventStore
from code_puppy.plugins.native_agents.reference_store import ReferenceStore
from code_puppy.plugins.native_agents.state_store import StateStore


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    limit: int = Field(ge=1, le=10)


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    count: int = Field(ge=0)


async def _setup(tmp_path, *, enabled=True):
    path = tmp_path / "native.sqlite3"
    state = StateStore(str(path))
    execution = ExecutionIdentity(
        execution_id="exec-cap",
        agent_name="agent",
        method_name="method",
        session_id="session",
        created_at=datetime.now(timezone.utc),
    )
    state.create_execution(
        execution, method_version=1, strategy=NativeStrategyName.PREDICT
    )
    events = EventStore(str(path))
    references = ReferenceStore(event_store=events)
    registry = CapabilityRegistry(references=references, event_store=events)
    spec = CapabilitySpec(
        name="search_results.count",
        resource_type="search_results",
        effect=CapabilityEffect.COMPUTE,
        input_model=Request,
        output_model=Result,
        description="count",
    )
    method = MethodSpec(
        name="method",
        strategy=NativeStrategyName.PREDICT,
        input_schema_name="Request",
        output_schema_name="Result",
        input_type=Request,
        output_type=Result,
        allowed_capabilities=(spec.name,),
    )
    handle = await references.create(
        resource_type="search_results",
        value=[1, 2, 3],
        preview=ReferencePreview(title="results", count=3, summary="three"),
        execution=execution,
    )
    return execution, events, references, registry, spec, method, handle


@pytest.mark.asyncio
async def test_registry_validates_and_audits_typed_invocation(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "code_puppy.plugins.native_agents.capability_policy.is_enabled", lambda: True
    )
    execution, events, references, registry, spec, method, handle = await _setup(
        tmp_path
    )
    registry.register(
        spec, lambda resource, request, _execution: Result(count=len(resource))
    )
    result = await registry.invoke(
        spec.name,
        handle,
        Request(limit=5),
        method=method,
        execution=execution,
    )
    assert result == Result(count=3)
    kinds = [
        event.kind for event in events.list_events(execution.execution_id, limit=10)
    ]
    assert kinds[-2:] == [
        NativeEventKind.CAPABILITY_REQUESTED,
        NativeEventKind.CAPABILITY_COMPLETED,
    ]


@pytest.mark.asyncio
async def test_registry_denies_undeclared_and_invalid_operations_before_adapter(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "code_puppy.plugins.native_agents.capability_policy.is_enabled", lambda: True
    )
    execution, events, references, registry, spec, method, handle = await _setup(
        tmp_path
    )
    called = False

    async def handler(*args):
        nonlocal called
        called = True
        return {"count": 1}  # type: ignore[return-value]

    registry.register(spec, handler)
    undeclared = method.model_copy(update={"allowed_capabilities": ()})
    with pytest.raises(CapabilityDeniedError):
        await registry.invoke(
            spec.name,
            handle,
            Result(count=1),  # type: ignore[arg-type]
            method=undeclared,
            execution=execution,
        )
    assert called is False
    with pytest.raises(CapabilityValidationError):
        await registry.invoke(
            spec.name,
            handle,
            Result(count=1),  # type: ignore[arg-type]
            method=method,
            execution=execution,
        )


@pytest.mark.asyncio
async def test_registry_audits_unavailable_handles_before_adapter(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "code_puppy.plugins.native_agents.capability_policy.is_enabled", lambda: True
    )
    execution, events, references, registry, spec, method, handle = await _setup(
        tmp_path
    )
    called = False

    def handler(*args):
        nonlocal called
        called = True
        return Result(count=1)

    registry.register(spec, handler)
    other_execution = execution.model_copy(update={"execution_id": "other"})
    StateStore(str(tmp_path / "native.sqlite3"), initialize=False).create_execution(
        other_execution,
        method_version=1,
        strategy=NativeStrategyName.PREDICT,
    )
    with pytest.raises(HandleUnavailableError):
        await registry.invoke(
            spec.name,
            handle,
            Request(limit=1),
            method=method,
            execution=other_execution,
        )
    assert called is False
    assert (
        events.list_events(other_execution.execution_id, limit=10)[-1].kind
        is NativeEventKind.CAPABILITY_DENIED
    )


@pytest.mark.asyncio
async def test_registry_normalizes_handler_failures_and_audits_them(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "code_puppy.plugins.native_agents.capability_policy.is_enabled", lambda: True
    )
    execution, events, references, registry, spec, method, handle = await _setup(
        tmp_path
    )

    def handler(*args):
        raise RuntimeError("private implementation detail")

    registry.register(spec, handler)
    with pytest.raises(CapabilityValidationError, match="capability invocation failed"):
        await registry.invoke(
            spec.name,
            handle,
            Request(limit=1),
            method=method,
            execution=execution,
        )
    assert (
        events.list_events(execution.execution_id, limit=10)[-1].payload["outcome"]
        == "failed"
    )
    assert (
        "private implementation"
        not in events.list_events(execution.execution_id, limit=10)[
            -1
        ].model_dump_json()
    )


@pytest.mark.asyncio
async def test_undeclared_capability_is_denied_before_handle_lookup(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "code_puppy.plugins.native_agents.capability_policy.is_enabled", lambda: True
    )
    execution, _events, references, registry, spec, method, handle = await _setup(
        tmp_path
    )
    registry.register(
        spec, lambda resource, request, _execution: Result(count=len(resource))
    )
    undeclared = method.model_copy(update={"allowed_capabilities": ()})

    async def fail_lookup(*args, **kwargs):
        raise AssertionError("denied declarations must not inspect handles")

    monkeypatch.setattr(references, "describe", fail_lookup)
    with pytest.raises(CapabilityDeniedError):
        await registry.invoke(
            spec.name,
            handle,
            Request(limit=1),
            method=undeclared,
            execution=execution,
        )
