from datetime import datetime, timezone

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from code_puppy.plugins.native_agents.contracts import (
    ContextBudget,
    ExecutionIdentity,
    MethodSpec,
    NativeEvent,
    NativeEventKind,
    NativeStrategyName,
)


UTC_NOW = datetime.now(timezone.utc)


def test_boundary_models_reject_unknown_fields():
    with pytest.raises(ValidationError):
        ExecutionIdentity(
            execution_id="exec-1",
            agent_name="agent",
            method_name="review",
            created_at=UTC_NOW,
            unexpected="nope",
        )

    with pytest.raises(ValidationError):
        NativeEvent(
            event_id="event-1",
            execution_id="exec-1",
            sequence=1,
            kind=NativeEventKind.EXECUTION_STARTED,
            occurred_at=UTC_NOW,
            payload={},
            unexpected="nope",
        )

    with pytest.raises(ValidationError):
        MethodSpec(
            name="review",
            strategy=NativeStrategyName.PREDICT,
            input_schema_name="Input",
            output_schema_name="Output",
            unexpected="nope",
        )


def test_invalid_enum_and_budget_values_are_rejected():
    with pytest.raises(ValidationError):
        MethodSpec(
            name="review",
            strategy="not-a-strategy",
            input_schema_name="Input",
            output_schema_name="Output",
        )

    with pytest.raises(ValidationError):
        ContextBudget(max_chars=255, max_events=1, max_preview_items=1)

    with pytest.raises(ValidationError):
        ContextBudget(max_chars=256, max_events=501, max_preview_items=1)


def test_models_round_trip_timezone_revision_and_schema():
    identity = ExecutionIdentity(
        execution_id="exec-1",
        agent_name="agent",
        method_name="review",
        created_at=UTC_NOW,
    )
    restored = ExecutionIdentity.model_validate_json(identity.model_dump_json())
    assert restored == identity
    assert restored.created_at.tzinfo is not None


def test_capability_names_require_namespaced_identifiers():
    from code_puppy.plugins.native_agents.contracts import (
        CapabilityEffect,
        CapabilitySpec,
    )

    with pytest.raises(ValidationError):
        CapabilitySpec(
            name="read_file",
            resource_type="search_results",
            effect=CapabilityEffect.OBSERVE,
            description="invalid namespace",
        )


def test_secret_field_metadata_is_allowed_on_strict_models():
    class State(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        token: str = Field(json_schema_extra={"secret": True})

    assert State(token="value").token == "value"
