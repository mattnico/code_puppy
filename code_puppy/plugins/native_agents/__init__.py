"""Opt-in native-agent runtime for typed, durable agent methods.

The package exposes contracts and storage primitives without performing I/O or
registering callbacks at import time. The callback entry point lives in
``register_callbacks.py`` and is intentionally disabled by default.
"""

from .contracts import (
    CapabilityEffect,
    ContextBudget,
    ExecutionIdentity,
    MethodSpec,
    NativeEvent,
    NativeEventKind,
    NativeExecutionRecord,
    NativeExecutionStatus,
    NativeStateEnvelope,
    NativeStrategyName,
)
from .errors import (
    EventStoreError,
    NativeAgentError,
    NativeContractError,
    NativeRuntimeDisabledError,
    NativeStorageUnavailableError,
    StateConflictError,
    StateSchemaError,
)

__all__ = [
    "CapabilityEffect",
    "ContextBudget",
    "EventStoreError",
    "ExecutionIdentity",
    "MethodSpec",
    "NativeAgentError",
    "NativeContractError",
    "NativeEvent",
    "NativeEventKind",
    "NativeExecutionRecord",
    "NativeExecutionStatus",
    "NativeRuntimeDisabledError",
    "NativeStateEnvelope",
    "NativeStorageUnavailableError",
    "NativeStrategyName",
    "StateConflictError",
    "StateSchemaError",
]
