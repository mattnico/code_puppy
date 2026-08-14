"""Explicit typed capability registry and host-side invocation boundary."""

from __future__ import annotations

import asyncio
import inspect
import json
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
from .execution import current_execution
from .reference_store import ReferenceStore, handle_id_hash

CapabilityHandler = Callable[[Any, BaseModel, ExecutionIdentity], Any]


def _strict_boundary_model(model: type[BaseModel]) -> bool:
    config = getattr(model, "model_config", {})
    return (
        isinstance(config, dict)
        and config.get("extra") == "forbid"
        and config.get("strict") is True
    )


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
        if not _strict_boundary_model(spec.input_model) or not _strict_boundary_model(
            spec.output_model
        ):
            raise CapabilityValidationError("capability models are not strict")
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
        active_execution = current_execution(required=False)
        if active_execution is not None and (
            active_execution.execution_id != execution.execution_id
        ):
            raise CapabilityDeniedError("capability execution is not active")
        capability_name = name if isinstance(name, str) else "<invalid>"
        supplied_id = (
            handle.handle_id
            if isinstance(handle, ReferenceHandle)
            else handle
            if isinstance(handle, str)
            else "<invalid>"
        )
        hashed_id = handle_id_hash(supplied_id[:256])
        spec, handler = self._capabilities.get(capability_name, (None, None))
        if spec is None or handler is None:
            self._event(
                execution.execution_id,
                NativeEventKind.CAPABILITY_REQUESTED,
                {
                    "capability": capability_name[:200],
                    "effect": "unknown",
                    "handle_id_hash": hashed_id,
                },
            )
            self._record_denial(
                execution.execution_id,
                capability_name[:200],
                "unknown",
                hashed_id,
                "capability_unavailable",
            )
            raise CapabilityNotFoundError("capability is unavailable")
        if not _strict_boundary_model(spec.input_model) or not _strict_boundary_model(
            spec.output_model
        ):
            raise CapabilityValidationError("capability models are not strict")
        self._event(
            execution.execution_id,
            NativeEventKind.CAPABILITY_REQUESTED,
            {
                "capability": spec.name,
                "effect": spec.effect.value,
                "handle_id_hash": hashed_id,
            },
        )
        declaration_decision = self.policy.authorize_declaration(
            method=method,
            execution=execution,
            capability=spec,
        )
        if not declaration_decision.allowed:
            self._record_denial(
                execution.execution_id,
                spec.name,
                spec.effect.value,
                hashed_id,
                declaration_decision.reason,
            )
            raise CapabilityDeniedError(declaration_decision.reason)
        try:
            validated_request = spec.input_model.model_validate(
                request.model_dump(mode="python")
            )
        except (AttributeError, TypeError, ValidationError) as exc:
            self._record_denial(
                execution.execution_id,
                spec.name,
                spec.effect.value,
                hashed_id,
                "invalid_request",
            )
            raise CapabilityValidationError("capability request is invalid") from exc
        try:
            metadata = await self.references.describe(
                handle,
                execution=execution,
                expected_type=spec.resource_type,
            )
        except HandleUnavailableError:
            self._record_denial(
                execution.execution_id,
                spec.name,
                spec.effect.value,
                hashed_id,
                "handle_unavailable",
            )
            raise
        decision = self.policy.authorize(
            method=method,
            execution=execution,
            handle=metadata,
            capability=spec,
        )
        if not decision.allowed:
            self._record_denial(
                execution.execution_id,
                spec.name,
                spec.effect.value,
                hashed_id,
                decision.reason,
            )
            raise CapabilityDeniedError(decision.reason)
        try:
            resource = await self.references.resolve(
                metadata,
                execution=execution,
                expected_type=spec.resource_type,
            )
        except HandleUnavailableError:
            self._record_denial(
                execution.execution_id,
                spec.name,
                spec.effect.value,
                hashed_id,
                "handle_unavailable",
            )
            raise
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
            try:
                json.dumps(validated_result.model_dump(mode="json"), allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise CapabilityValidationError(
                    "capability result is not JSON-compatible"
                ) from exc
            derived_handle = getattr(validated_result, "handle", None)
            if isinstance(derived_handle, ReferenceHandle) and (
                derived_handle.execution_id != execution.execution_id
                or derived_handle.owner_session_id != execution.session_id
            ):
                raise CapabilityValidationError(
                    "capability returned an out-of-scope handle"
                )
        except asyncio.CancelledError:
            self._event(
                execution.execution_id,
                NativeEventKind.CAPABILITY_COMPLETED,
                {
                    "capability": spec.name,
                    "handle_id_hash": hashed_id,
                    "outcome": "cancelled",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            raise
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
        if isinstance(derived_handle, ReferenceHandle):
            completion_payload.update(
                {
                    "derived_handle_id_hash": handle_id_hash(derived_handle.handle_id),
                    "parent_handle_id_hash": hashed_id,
                    "operation": spec.name,
                }
            )
        self._event(
            execution.execution_id,
            NativeEventKind.CAPABILITY_COMPLETED,
            completion_payload,
        )
        return validated_result

    def _record_denial(
        self,
        execution_id: str,
        capability: str,
        effect: str,
        handle_id_hash_value: str,
        reason: str,
    ) -> None:
        self._event(
            execution_id,
            NativeEventKind.CAPABILITY_DENIED,
            {
                "capability": capability,
                "effect": effect,
                "handle_id_hash": handle_id_hash_value,
                "reason": reason,
            },
        )

    def _event(
        self, execution_id: str, kind: NativeEventKind, payload: dict[str, Any]
    ) -> None:
        self.event_store.append(execution_id, kind, payload)
