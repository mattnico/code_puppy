"""Opt-in native-agent runtime for typed, durable agent methods.

The package exposes contracts and storage primitives without performing I/O or
registering callbacks at import time. The callback entry point lives in
``register_callbacks.py`` and is intentionally disabled by default.
"""

from .capabilities import CapabilityRegistry
from .capability_policy import CapabilityPolicy
from .contracts import (
    AuthorizationDecision,
    CapabilityEffect,
    CapabilitySpec,
    ContextBlock,
    ContextBudget,
    EventQuery,
    EventSummary,
    ExecutionIdentity,
    MethodSpec,
    NativeEvent,
    NativeEventKind,
    NativeExecutionRecord,
    NativeExecutionStatus,
    NativeStateEnvelope,
    NativeStrategyName,
    ReferenceHandle,
    ReferencePreview,
    SearchCountRequest,
    SearchCounts,
    SearchHandleResult,
    SearchMatch,
    SearchPage,
    SearchPageRequest,
    SearchPathCount,
    SearchPrefixRequest,
    SearchResultSet,
    SearchSampleRequest,
)
from .errors import (
    CapabilityDeniedError,
    CapabilityNotFoundError,
    CapabilityValidationError,
    EventStoreError,
    HandleUnavailableError,
    NativeAgentError,
    NativeContractError,
    NativeOutputValidationError,
    NativeRuntimeDisabledError,
    NativeStorageUnavailableError,
    StateConflictError,
    StateSchemaError,
)
from .events import EventService, EventStore
from .method import NativeAgentMixin, native_method
from .predict import PredictStrategy
from .reference_store import ReferenceStore
from .runtime import NativeMethodRuntime
from .state import StateService

__all__ = [
    "AuthorizationDecision",
    "CapabilityDeniedError",
    "CapabilityEffect",
    "CapabilityNotFoundError",
    "CapabilityPolicy",
    "CapabilityRegistry",
    "CapabilitySpec",
    "CapabilityValidationError",
    "ContextBlock",
    "ContextBudget",
    "EventQuery",
    "EventService",
    "EventStore",
    "EventStoreError",
    "EventSummary",
    "ExecutionIdentity",
    "HandleUnavailableError",
    "MethodSpec",
    "NativeAgentError",
    "NativeAgentMixin",
    "NativeContractError",
    "NativeEvent",
    "NativeEventKind",
    "NativeExecutionRecord",
    "NativeExecutionStatus",
    "NativeMethodRuntime",
    "NativeOutputValidationError",
    "NativeRuntimeDisabledError",
    "NativeStateEnvelope",
    "NativeStorageUnavailableError",
    "NativeStrategyName",
    "PredictStrategy",
    "ReferenceHandle",
    "ReferencePreview",
    "ReferenceStore",
    "SearchCountRequest",
    "SearchCounts",
    "SearchHandleResult",
    "SearchMatch",
    "SearchPage",
    "SearchPageRequest",
    "SearchPathCount",
    "SearchPrefixRequest",
    "SearchResultSet",
    "SearchSampleRequest",
    "StateConflictError",
    "StateSchemaError",
    "StateService",
    "native_method",
]
