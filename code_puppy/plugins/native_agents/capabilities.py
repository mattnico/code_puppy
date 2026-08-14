"""Explicit typed capability registry and host-side invocation boundary."""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from .capability_policy import CapabilityPolicy
from .contracts import (
    CapabilitySpec,
    ExecutionIdentity,
    MethodSpec,
    NativeEventKind,
    ReferenceHandle,
)
from .errors import (
    CapabilityDeniedError,
    CapabilityNotFoundError,
    CapabilityValidationError,
    HandleUnavailableError,
)
from .events import EventStore
from .reference_store import ReferenceStore, handle_id_hash

CapabilityHandler = Callable[[Any, BaseModel, ExecutionIdentity], Any]


class CapabilityRegistry:
    """Namespaced capabilities with typed request/response validation."""

    def __init__(
        self,
        *,
        references: ReferenceStore,
        event_store: EventStore,
        policy: CapabilityPolicy | None = None,
    ) -> None:
        self.references = references
        self.event_store = event_store
        self.policy = policy or CapabilityPolicy()
        self._capabilities: dict[str, tuple[CapabilitySpec, CapabilityHandler]] = {}

    def register(self, spec: CapabilitySpec, handler: CapabilityHandler) -> None:
        if spec.name in self._capabilities:
            raise ValueError(f"capability {spec.name!r} is already registered")
        if not callable(handler):
            raise TypeError("capability handler must be callable")
        self._capabilities[spec.name] = (spec, handler)

    def get(self, name: str) -> CapabilitySpec:
        try:
            return self._capabilities[name][0]
        except KeyError as exc:
            raise CapabilityNotFoundError("capability is unavailable") from exc

    async def invoke(
        self,
        name: str,
        handle: ReferenceHandle | str,
        request: BaseModel,
        *,
        method: MethodSpec,
        execution: ExecutionIdentity,
    ) -> BaseModel:
        spec, handler = self._capabilities.get(name, (None, None))
        if spec is None or handler is None:
            raise CapabilityNotFoundError("capability is unavailable")
        supplied_id = (
            handle.handle_id if isinstance(handle, ReferenceHandle) else str(handle)
        )
        hashed_id = handle_id_hash(supplied_id)
        self._event(
            execution.execution_id,
            NativeEventKind.CAPABILITY_REQUESTED,
            {
                "capability": spec.name,
                "effect": spec.effect.value,
                "handle_id_hash": hashed_id,
            },
        )
        try:
            metadata = await self.references.describe(
                handle,
                execution=execution,
                expected_type=spec.resource_type,
            )
        except HandleUnavailableError:
            self._event(
                execution.execution_id,
                NativeEventKind.CAPABILITY_DENIED,
                {
                    "capability": spec.name,
                    "effect": spec.effect.value,
                    "handle_id_hash": hashed_id,
                    "reason": "handle_unavailable",
                },
            )
            raise
        decision = self.policy.authorize(
            method=method,
            execution=execution,
            handle=metadata,
            capability=spec,
        )
        if not decision.allowed:
            self._event(
                execution.execution_id,
                NativeEventKind.CAPABILITY_DENIED,
                {
                    "capability": spec.name,
                    "effect": spec.effect.value,
                    "handle_id_hash": hashed_id,
                    "reason": decision.reason,
                },
            )
            raise CapabilityDeniedError(decision.reason)
        try:
            validated_request = spec.input_model.model_validate(
                request.model_dump(mode="python")
            )
        except (AttributeError, TypeError, ValidationError) as exc:
            self._event(
                execution.execution_id,
                NativeEventKind.CAPABILITY_DENIED,
                {
                    "capability": spec.name,
                    "handle_id_hash": hashed_id,
                    "reason": "invalid_request",
                },
            )
            raise CapabilityValidationError("capability request is invalid") from exc
        resource = await self.references.resolve(
            metadata,
            execution=execution,
            expected_type=spec.resource_type,
        )
        started = time.monotonic()
        try:
            result = handler(resource, validated_request, execution)
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, BaseModel):
                raise CapabilityValidationError("capability returned an invalid result")
            validated_result = spec.output_model.model_validate(
                result.model_dump(mode="python")
            )
        except CapabilityValidationError:
            self._event(
                execution.execution_id,
                NativeEventKind.CAPABILITY_COMPLETED,
                {
                    "capability": spec.name,
                    "handle_id_hash": hashed_id,
                    "outcome": "failed",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            raise
        except (TypeError, ValueError, ValidationError) as exc:
            self._event(
                execution.execution_id,
                NativeEventKind.CAPABILITY_COMPLETED,
                {
                    "capability": spec.name,
                    "handle_id_hash": hashed_id,
                    "outcome": "failed",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            raise CapabilityValidationError("capability result is invalid") from exc
        except Exception as exc:
            self._event(
                execution.execution_id,
                NativeEventKind.CAPABILITY_COMPLETED,
                {
                    "capability": spec.name,
                    "handle_id_hash": hashed_id,
                    "outcome": "failed",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            raise CapabilityValidationError("capability invocation failed") from exc
        completion_payload = {
            "capability": spec.name,
            "handle_id_hash": hashed_id,
            "outcome": "succeeded",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
        derived_handle = getattr(validated_result, "handle", None)
        if isinstance(derived_handle, ReferenceHandle):
            completion_payload.update(
                {
                    "derived_handle_id_hash": handle_id_hash(derived_handle.handle_id),
                    "parent_handle_id_hash": hashed_id,
                }
            )
        self._event(
            execution.execution_id,
            NativeEventKind.CAPABILITY_COMPLETED,
            completion_payload,
        )
        return validated_result

    def _event(
        self, execution_id: str, kind: NativeEventKind, payload: dict[str, Any]
    ) -> None:
        self.event_store.append(execution_id, kind, payload)
