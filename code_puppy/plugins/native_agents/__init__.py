"""Opt-in native-agent runtime for typed, durable agent methods.

The package exposes contracts and storage primitives without performing I/O or
registering callbacks at import time. The callback entry point lives in
``register_callbacks.py`` and is intentionally disabled by default.
"""

from .contracts import (
    CapabilityEffect,
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
)
from .events import EventService, EventStore
from .errors import (
    EventStoreError,
    NativeAgentError,
    NativeContractError,
    NativeOutputValidationError,
    NativeRuntimeDisabledError,
    NativeStorageUnavailableError,
    StateConflictError,
    StateSchemaError,
)

__all__ = [
    "CapabilityEffect",
    "ContextBlock",
    "ContextBudget",
    "EventQuery",
    "EventService",
    "EventStore",
    "EventStoreError",
    "EventSummary",
    "ExecutionIdentity",
    "MethodSpec",
    "NativeAgentError",
    "NativeContractError",
    "NativeEvent",
    "NativeEventKind",
    "NativeExecutionRecord",
    "NativeExecutionStatus",
    "NativeOutputValidationError",
    "NativeRuntimeDisabledError",
    "NativeStateEnvelope",
    "NativeStorageUnavailableError",
    "NativeStrategyName",
    "StateConflictError",
    "StateSchemaError",
]
