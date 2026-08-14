from datetime import datetime, timezone

from code_puppy.plugins.native_agents.capability_policy import CapabilityPolicy
from code_puppy.plugins.native_agents.contracts import (
    CapabilityEffect,
    CapabilitySpec,
    ExecutionIdentity,
    MethodSpec,
    NativeStrategyName,
    ReferenceHandle,
    ReferencePreview,
)
from code_puppy.plugins.native_agents.demo_agent import (
    ChangeSummaryInput,
    ChangeSummaryResult,
)


def _execution(execution_id="exec", session_id="session"):
    return ExecutionIdentity(
        execution_id=execution_id,
        agent_name="agent",
        method_name="method",
        session_id=session_id,
        created_at=datetime.now(timezone.utc),
    )


def _method(*capabilities):
    return MethodSpec(
        name="method",
        strategy=NativeStrategyName.PREDICT,
        input_schema_name="Input",
        output_schema_name="Output",
        input_type=ChangeSummaryInput,
        output_type=ChangeSummaryResult,
        allowed_capabilities=capabilities,
    )


def _handle(execution):
    return ReferenceHandle(
        handle_id="a" * 32,
        resource_type="search_results",
        execution_id=execution.execution_id,
        owner_session_id=execution.session_id,
        created_at=execution.created_at,
        expires_at=execution.created_at,
        preview=ReferencePreview(title="results", summary="preview"),
    )


def _capability(effect=CapabilityEffect.OBSERVE):
    return CapabilitySpec(
        name="search_results.page",
        resource_type="search_results",
        effect=effect,
        input_model=ChangeSummaryInput,
        output_model=ChangeSummaryResult,
        description="page",
    )


def test_policy_requires_exact_declaration_scope_and_read_only_effect(monkeypatch):
    monkeypatch.setattr(
        "code_puppy.plugins.native_agents.capability_policy.is_enabled", lambda: True
    )
    execution = _execution()
    policy = CapabilityPolicy()
    allowed = policy.authorize(
        method=_method("search_results.page"),
        execution=execution,
        handle=_handle(execution),
        capability=_capability(),
    )
    assert allowed.allowed is True

    assert (
        policy.authorize(
            method=_method(),
            execution=execution,
            handle=_handle(execution),
            capability=_capability(),
        ).allowed
        is False
    )
    assert (
        policy.authorize(
            method=_method("search_results.page"),
            execution=_execution("other"),
            handle=_handle(execution),
            capability=_capability(),
        ).allowed
        is False
    )
    assert (
        policy.authorize(
            method=_method("search_results.page"),
            execution=execution,
            handle=_handle(execution),
            capability=_capability(CapabilityEffect.MODIFY),
        ).allowed
        is False
    )
