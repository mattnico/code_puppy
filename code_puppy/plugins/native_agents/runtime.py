"""Lifecycle coordinator for one typed native-method execution."""

from __future__ import annotations

import asyncio
import json
import uuid
import weakref
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .context import ContextRenderer, render_context_text
from .config import (
    context_max_chars,
    database_path,
    event_max_per_view,
    is_enabled,
)
from .contracts import (
    EventQuery,
    ExecutionIdentity,
    MethodSpec,
    NativeEventKind,
    NativeExecutionStatus,
    NativeStrategyName,
)
from .errors import (
    NativeContractError,
    NativeOutputValidationError,
    NativeRuntimeDisabledError,
    NativeStorageUnavailableError,
    StateSchemaError,
)
from .events import EventService, EventStore, redact_payload
from .execution import NativeExecutionContext, current_execution, execution_scope
from .integrations.sessions import session_id
from .predict import PredictStrategy
from .reference_store import ReferenceStore
from .state import StateService
from .state_store import StateStore
from .capabilities import CapabilityRegistry
from .capability_adapters.search_results import register_search_result_capabilities
from .storage import initialize_database


class NativeMethodRuntime:
    """Create durable records around an isolated typed strategy invocation."""

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        predict_strategy: PredictStrategy | None = None,
    ) -> None:
        self.db_path = str(db_path or database_path())
        self.predict_strategy = predict_strategy or PredictStrategy()
        self._locks: weakref.WeakKeyDictionary[Any, asyncio.Lock] = (
            weakref.WeakKeyDictionary()
        )

    def _lock_for(self, parent_agent: Any) -> asyncio.Lock:
        try:
            return self._locks.setdefault(parent_agent, asyncio.Lock())
        except TypeError:
            # A small fallback for unusual test doubles that cannot be weakly
            # referenced. It still serializes calls through this runtime.
            if not hasattr(self, "_fallback_lock"):
                self._fallback_lock = asyncio.Lock()
            return self._fallback_lock

    async def execute(self, parent_agent: Any, spec: MethodSpec, payload: Any) -> Any:
        """Execute a declared native method or fail closed before model work."""

        if not isinstance(payload, spec.input_type):
            raise NativeContractError("native payload does not match input_type")
        if not is_enabled():
            raise NativeRuntimeDisabledError("native agents are disabled")
        if spec.strategy is not NativeStrategyName.PREDICT:
            raise NativeRuntimeDisabledError("native strategy is not available")
        try:
            initialize_database(self.db_path)
            state_store = StateStore(self.db_path, initialize=False)
            event_store = EventStore(self.db_path)
        except NativeStorageUnavailableError:
            raise
        except Exception as exc:
            raise NativeStorageUnavailableError(
                "native storage is unavailable"
            ) from exc

        async with self._lock_for(parent_agent):
            return await self._execute_locked(
                parent_agent, spec, payload, state_store, event_store
            )

    async def _revoke_references(
        self, references: ReferenceStore, execution_id: str
    ) -> None:
        try:
            await references.revoke_execution(execution_id)
        except Exception:
            # Cleanup must never replace the native result/error. The store has
            # already removed live values before emitting optional audit events.
            return

    async def _execute_locked(
        self,
        parent_agent: Any,
        spec: MethodSpec,
        payload: Any,
        state_store: StateStore,
        event_store: EventStore,
    ) -> Any:
        parent_execution = current_execution(required=False)
        identity = ExecutionIdentity(
            execution_id=str(uuid.uuid4()),
            agent_name=parent_agent.name,
            method_name=spec.name,
            session_id=session_id(parent_agent),
            parent_execution_id=(
                parent_execution.execution_id if parent_execution else None
            ),
            created_at=datetime.now(timezone.utc),
        )
        references = ReferenceStore(event_store=event_store)
        capabilities = CapabilityRegistry(
            references=references,
            event_store=event_store,
        )
        register_search_result_capabilities(capabilities, references)
        event_service = EventService(identity.execution_id, event_store)
        state_service: StateService | None = None
        execution_created = False
        try:
            state_store.create_execution(
                identity,
                method_version=spec.version,
                strategy=spec.strategy,
            )
            execution_created = True
            event_store.append(
                identity.execution_id,
                NativeEventKind.EXECUTION_STARTED,
                {
                    "agent_name": identity.agent_name,
                    "method_name": identity.method_name,
                },
            )
            if spec.state_type is not None:
                try:
                    initial_state = (
                        spec.state_factory(payload)
                        if spec.state_factory is not None
                        else spec.state_type()
                    )
                    state_service = StateService(
                        execution_id=identity.execution_id,
                        state_type=spec.state_type,
                        state_store=state_store,
                        event_store=event_store,
                        schema_version=spec.state_schema_version,
                    )
                    state_service.initialize(initial_state)
                except Exception as exc:
                    if isinstance(exc, StateSchemaError):
                        raise
                    raise StateSchemaError(
                        "native state initialization failed"
                    ) from exc
            native_context = NativeExecutionContext(
                identity=identity,
                method=spec,
                state=state_service,
                events=event_service,
                context=ContextRenderer(),
                references=references,
                capabilities=capabilities,
            )
            state_store.set_execution_status(
                identity.execution_id, NativeExecutionStatus.RUNNING
            )
            execution = state_store.get_execution(identity.execution_id)
            if execution is None:  # pragma: no cover - storage invariant
                raise NativeStorageUnavailableError(
                    "native execution record disappeared"
                )
            state_snapshot = state_store.get_state(identity.execution_id)
            if state_service is not None:
                state_service.get()
            event_limit = spec.context_budget.max_events
            event_summaries = (
                event_store.recent_summaries(
                    identity.execution_id,
                    EventQuery(limit=max(1, event_limit)),
                )
                if event_limit
                else []
            )
            effective_budget = spec.context_budget.model_copy(
                update={
                    "max_chars": min(
                        spec.context_budget.max_chars, context_max_chars()
                    ),
                    "max_events": min(
                        spec.context_budget.max_events, event_max_per_view()
                    ),
                }
            )
            reference_items = await references.list_for_execution(
                identity, limit=effective_budget.max_preview_items
            )
            context_view = ContextRenderer().render(
                spec=spec,
                execution=execution,
                state=state_snapshot,
                events=event_summaries,
                budget=effective_budget,
                references=reference_items,
            )
            event_store.append(
                identity.execution_id,
                NativeEventKind.CONTEXT_RENDERED,
                {
                    "total_chars": context_view.total_chars,
                    "truncated": context_view.truncated,
                    "blocks": [block.name for block in context_view.blocks],
                },
            )
        except asyncio.CancelledError:
            if execution_created:
                await self._record_failure_best_effort(
                    state_store,
                    event_store,
                    identity,
                    "cancelled",
                    "native execution cancelled during setup",
                    NativeExecutionStatus.CANCELLED,
                )
                await self._revoke_references(references, identity.execution_id)
            raise
        except Exception as exc:
            if execution_created:
                await self._record_failure_best_effort(
                    state_store,
                    event_store,
                    identity,
                    getattr(exc, "code", "native_setup_failed"),
                    str(exc),
                    NativeExecutionStatus.FAILED,
                )
                await self._revoke_references(references, identity.execution_id)
            raise

        validation_event_recorded = False

        def record_validation_failure(attempt: int, error_code: str):
            nonlocal validation_event_recorded
            validation_event_recorded = True
            return event_store.append(
                identity.execution_id,
                NativeEventKind.VALIDATION_FAILED,
                {"attempt": attempt, "error_code": error_code},
            )

        try:
            async with execution_scope(identity):
                result = await self.predict_strategy.execute(
                    parent_agent,
                    spec,
                    payload,
                    execution_id=identity.execution_id,
                    context_text=render_context_text(context_view),
                    native_context=native_context,
                    on_validation_failure=record_validation_failure,
                )
            if not isinstance(result, BaseModel):
                raise NativeOutputValidationError(
                    "native strategy returned a non-Pydantic output"
                )
            try:
                validated_result = spec.output_type.model_validate(
                    result.model_dump(mode="python")
                )
            except (TypeError, ValueError, ValidationError) as exc:
                raise NativeOutputValidationError(
                    "native strategy returned an invalid declared output"
                ) from exc
            if type(validated_result) is not spec.output_type:
                raise NativeOutputValidationError(
                    "native strategy returned an invalid declared output"
                )
            try:
                json.dumps(validated_result.model_dump(mode="json"), allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise NativeOutputValidationError(
                    "native strategy returned a non-JSON output"
                ) from exc
            state_store.set_execution_status(
                identity.execution_id,
                NativeExecutionStatus.FINISHED,
                events=(
                    (
                        NativeEventKind.EXECUTION_FINISHED,
                        {"status": NativeExecutionStatus.FINISHED.value},
                    ),
                ),
            )
            return validated_result
        except asyncio.CancelledError:
            await self._record_failure(
                state_store,
                event_store,
                identity,
                "cancelled",
                "native execution cancelled",
                NativeExecutionStatus.CANCELLED,
            )
            raise
        except NativeOutputValidationError as exc:
            await self._record_failure(
                state_store,
                event_store,
                identity,
                exc.code,
                str(exc),
                NativeExecutionStatus.FAILED,
                validation_failed=not validation_event_recorded,
            )
            raise
        except Exception as exc:
            await self._record_failure(
                state_store,
                event_store,
                identity,
                getattr(exc, "code", "native_execution_failed"),
                str(exc),
                NativeExecutionStatus.FAILED,
            )
            raise
        finally:
            await self._revoke_references(references, identity.execution_id)

    async def _record_failure_best_effort(
        self,
        state_store: StateStore,
        event_store: EventStore,
        identity: ExecutionIdentity,
        code: str,
        summary: str,
        status: NativeExecutionStatus,
    ) -> None:
        try:
            await self._record_failure(
                state_store,
                event_store,
                identity,
                code,
                summary,
                status,
            )
        except Exception:
            # The original setup failure is more useful than a secondary
            # storage/cleanup failure, and the feature remains fail-closed.
            return

    async def _record_failure(
        self,
        state_store: StateStore,
        event_store: EventStore,
        identity: ExecutionIdentity,
        code: str,
        summary: str,
        status: NativeExecutionStatus,
        *,
        validation_failed: bool = False,
    ) -> None:
        bounded_summary = str(summary).replace("\n", " ")[:500]
        safe_summary = redact_payload({"summary": bounded_summary})["summary"]
        if not isinstance(safe_summary, str):
            safe_summary = "native execution failed"
        if validation_failed:
            event_store.append(
                identity.execution_id,
                NativeEventKind.VALIDATION_FAILED,
                {"error_code": code, "summary": safe_summary},
            )
        terminal_events = [
            (
                NativeEventKind.EXECUTION_FAILED,
                {
                    "error_code": code,
                    "summary": safe_summary,
                    "status": status.value,
                },
            )
        ]
        state_store.set_execution_status(
            identity.execution_id,
            status,
            error_code=code,
            error_summary=safe_summary,
            events=tuple(terminal_events),
        )
