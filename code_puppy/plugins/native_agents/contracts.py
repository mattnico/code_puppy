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
    error_summary: str | None = Field(default=None, max_length=2_000)


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


class EventQuery(_StrictModel, frozen=True):
    kinds: tuple[NativeEventKind, ...] = ()
    after_sequence: int | None = Field(default=None, ge=0)
    limit: int = Field(default=20, ge=1, le=500)
    include_payload: bool = False


class EventSummary(_StrictModel, frozen=True):
    sequence: int = Field(ge=1)
    kind: NativeEventKind
    occurred_at: datetime
    summary: str = Field(max_length=500)


class ContextBlock(_StrictModel, frozen=True):
    name: str = Field(min_length=1, max_length=100)
    priority: int
    content: str = Field(max_length=100_000)
    source: str = Field(min_length=1, max_length=50)
    truncated: bool = False


class NativeContextView(_StrictModel, frozen=True):
    execution_id: str
    blocks: list[ContextBlock]
    total_chars: int = Field(ge=0)
    truncated: bool = False


class ReferencePreview(_StrictModel, frozen=True):
    title: str = Field(min_length=1, max_length=200)
    count: int | None = Field(default=None, ge=0)
    summary: str = Field(max_length=1_000)
    sample: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=25)
    truncated: bool = False


class ReferenceHandle(_StrictModel, frozen=True):
    handle_id: str = Field(min_length=20, max_length=256)
    resource_type: str = Field(min_length=1, max_length=200)
    execution_id: str = Field(min_length=1, max_length=128)
    owner_session_id: str | None = Field(default=None, max_length=256)
    created_at: datetime
    expires_at: datetime
    preview: ReferencePreview


class CapabilitySpec(_StrictModel, frozen=True):
    """Runtime declaration; model classes are never persisted."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        arbitrary_types_allowed=True,
    )

    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$", max_length=200)
    resource_type: str = Field(min_length=1, max_length=200)
    effect: CapabilityEffect
    input_model: type[BaseModel] = BaseModel
    output_model: type[BaseModel] = BaseModel
    description: str = Field(min_length=1, max_length=1_000)
    version: int = Field(default=1, ge=1)


class AuthorizationDecision(_StrictModel, frozen=True):
    allowed: bool
    reason: str = Field(max_length=200)


class SearchMatch(_StrictModel, frozen=True):
    path: str = Field(min_length=1, max_length=2_000)
    line_number: int | None = Field(default=None, ge=1)
    snippet: str = Field(max_length=8_000)


class SearchResultSet(_StrictModel, frozen=True):
    query: str = Field(min_length=1, max_length=1_000)
    matches: list[SearchMatch] = Field(max_length=100_000)
    source_root_label: str = Field(min_length=1, max_length=500)


class SearchPageRequest(_StrictModel, frozen=True):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=25, ge=1, le=50)


class SearchPage(_StrictModel, frozen=True):
    offset: int = Field(ge=0)
    matches: list[SearchMatch] = Field(max_length=50)
    total: int = Field(ge=0)


class SearchPrefixRequest(_StrictModel, frozen=True):
    prefix: str = Field(min_length=1, max_length=500)


class SearchCountRequest(_StrictModel, frozen=True):
    max_groups: int = Field(default=100, ge=1, le=100)


class SearchPathCount(_StrictModel, frozen=True):
    prefix: str
    count: int = Field(ge=0)


class SearchCounts(_StrictModel, frozen=True):
    groups: list[SearchPathCount] = Field(max_length=100)


class SearchSampleRequest(_StrictModel, frozen=True):
    seed: int = Field(default=0, ge=0)
    limit: int = Field(default=10, ge=1, le=25)


class SearchHandleResult(_StrictModel, frozen=True):
    handle: ReferenceHandle


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
