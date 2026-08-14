"""Strict public contracts for the native-agent runtime."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypeAliasType

JsonValue = TypeAliasType(
    "JsonValue",
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"],
)


class _StrictModel(BaseModel):
    """Base for external and persisted native-agent data."""

    model_config = ConfigDict(extra="forbid", strict=True)


class NativeStrategyName(str, Enum):
    PREDICT = "predict"
    CODEACT = "codeact"


class NativeEventKind(str, Enum):
    EXECUTION_STARTED = "execution_started"
    EXECUTION_FINISHED = "execution_finished"
    EXECUTION_FAILED = "execution_failed"
    STATE_INITIALIZED = "state_initialized"
    STATE_UPDATED = "state_updated"
    CONTEXT_RENDERED = "context_rendered"
    VALIDATION_FAILED = "validation_failed"
    CAPABILITY_REQUESTED = "capability_requested"
    CAPABILITY_COMPLETED = "capability_completed"
    CAPABILITY_DENIED = "capability_denied"
    HANDLE_CREATED = "handle_created"
    HANDLE_EXPIRED = "handle_expired"
    CODE_CELL_STARTED = "code_cell_started"
    CODE_CELL_FINISHED = "code_cell_finished"
    CODE_CELL_FAILED = "code_cell_failed"


class CapabilityEffect(str, Enum):
    OBSERVE = "observe"
    COMPUTE = "compute"
    PROPOSE = "propose"
    MODIFY = "modify"
    EXECUTE = "execute"
    SECRET = "secret"


class NativeExecutionStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionIdentity(_StrictModel, frozen=True):
    execution_id: str = Field(min_length=1, max_length=128)
    agent_name: str = Field(min_length=1, max_length=200)
    method_name: str = Field(min_length=1, max_length=200)
    session_id: str | None = Field(default=None, max_length=256)
    parent_execution_id: str | None = Field(default=None, max_length=128)
    created_at: datetime


class NativeExecutionRecord(_StrictModel, frozen=True):
    execution_id: str
    agent_name: str
    method_name: str
    method_version: int = Field(ge=1)
    strategy: NativeStrategyName
    session_id: str | None = None
    parent_execution_id: str | None = None
    status: NativeExecutionStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_summary: str | None = Field(default=None, max_length=2000)


class NativeStateEnvelope(_StrictModel, frozen=True):
    execution_id: str
    schema_name: str = Field(min_length=1, max_length=256)
    schema_version: int = Field(ge=1)
    revision: int = Field(ge=1)
    state_json: dict[str, JsonValue]
    created_at: datetime
    updated_at: datetime


class NativeEvent(_StrictModel, frozen=True):
    event_id: str
    execution_id: str
    sequence: int = Field(ge=1)
    kind: NativeEventKind
    occurred_at: datetime
    payload: dict[str, JsonValue]
    redacted: bool = False


class ContextBudget(_StrictModel, frozen=True):
    max_chars: int = Field(ge=256, le=100_000)
    max_events: int = Field(ge=0, le=500)
    max_preview_items: int = Field(ge=0, le=100)


class MethodSpec(_StrictModel, frozen=True):
    """Immutable declaration metadata; model classes are runtime-only."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        arbitrary_types_allowed=True,
    )

    name: str = Field(min_length=1, max_length=200)
    version: int = Field(default=1, ge=1)
    strategy: NativeStrategyName
    input_schema_name: str = Field(min_length=1, max_length=256)
    output_schema_name: str = Field(min_length=1, max_length=256)
    state_schema_name: str | None = Field(default=None, max_length=256)
    allowed_capabilities: tuple[str, ...] = ()
    input_type: type[BaseModel] = BaseModel
    output_type: type[BaseModel] = BaseModel
    state_type: type[BaseModel] | None = None
    max_validation_repairs: int = Field(default=1, ge=0, le=3)
    context_budget: ContextBudget = Field(
        default_factory=lambda: ContextBudget(
            max_chars=12_000,
            max_events=30,
            max_preview_items=0,
        )
    )
    instructions: str = Field(default="", max_length=8_000)
