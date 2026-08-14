"""Typed failures raised by the native-agent runtime."""

from __future__ import annotations


class NativeAgentError(Exception):
    """Base class for predictable native-agent failures."""

    code = "native_agent_error"


class NativeRuntimeDisabledError(NativeAgentError):
    code = "native_runtime_disabled"


class NativeContractError(NativeAgentError):
    code = "native_contract_error"


class NativeOutputValidationError(NativeAgentError):
    code = "native_output_validation_failed"


class StateConflictError(NativeAgentError):
    code = "state_conflict"


class StateSchemaError(NativeAgentError):
    code = "state_schema_error"


class EventStoreError(NativeAgentError):
    code = "event_store_error"


class NativeStorageUnavailableError(NativeAgentError):
    code = "native_storage_unavailable"


class NoActiveExecutionError(NativeAgentError):
    code = "no_active_execution"
