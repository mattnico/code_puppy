"""Lifecycle coordinator for one typed native-method execution."""

from __future__ import annotations

import asyncio
import uuid
import weakref
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

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
from .events import EventStore, redact_payload
from .execution import current_execution, execution_scope
from .predict import PredictStrategy
from .state_store import StateStore
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
            session_id=getattr(parent_agent, "native_session_id", None),
            parent_execution_id=(
                parent_execution.execution_id if parent_execution else None
            ),
            created_at=datetime.now(timezone.utc),
        )
        state_store.create_execution(
            identity,
            method_version=spec.version,
            strategy=spec.strategy,
        )
        event_store.append(
            identity.execution_id,
            NativeEventKind.EXECUTION_STARTED,
            {"agent_name": identity.agent_name, "method_name": identity.method_name},
        )
        if spec.state_type is not None:
            try:
                initial_state = spec.state_type()
                state_store.initialize_state(
                    identity.execution_id,
                    initial_state,
                    schema_name=spec.state_type.__name__,
                    schema_version=1,
                )
            except (ValidationError, StateSchemaError) as exc:
                return await self._fail(
                    state_store,
                    event_store,
                    identity,
                    "state_initialization_failed",
                    str(exc),
                    exc,
                )
        state_store.set_execution_status(
            identity.execution_id, NativeExecutionStatus.RUNNING
        )
        execution = state_store.get_execution(identity.execution_id)
        if execution is None:  # pragma: no cover - storage invariant
            raise NativeStorageUnavailableError("native execution record disappeared")
        state_snapshot = state_store.get_state(identity.execution_id)
        event_limit = spec.context_budget.max_events
        event_summaries = (
            event_store.summaries(
                identity.execution_id,
                EventQuery(limit=max(1, event_limit)),
            )
            if event_limit
            else []
        )
        effective_budget = spec.context_budget.model_copy(
            update={
                "max_chars": min(spec.context_budget.max_chars, context_max_chars()),
                "max_events": min(spec.context_budget.max_events, event_max_per_view()),
            }
        )
        context_view = ContextRenderer().render(
            spec=spec,
            execution=execution,
            state=state_snapshot,
            events=event_summaries,
            budget=effective_budget,
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
                    on_validation_failure=record_validation_failure,
                )
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

        state_store.set_execution_status(
            identity.execution_id, NativeExecutionStatus.FINISHED
        )
        event_store.append(
            identity.execution_id,
            NativeEventKind.EXECUTION_FINISHED,
            {"status": NativeExecutionStatus.FINISHED.value},
        )
        return result

    async def _fail(
        self,
        state_store: StateStore,
        event_store: EventStore,
        identity: ExecutionIdentity,
        code: str,
        summary: str,
        error: Exception,
    ) -> Any:
        await self._record_failure(
            state_store,
            event_store,
            identity,
            code,
            summary,
            NativeExecutionStatus.FAILED,
        )
        raise error

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
        state_store.set_execution_status(
            identity.execution_id,
            status,
            error_code=code,
            error_summary=safe_summary,
        )
        event_store.append(
            identity.execution_id,
            NativeEventKind.EXECUTION_FAILED,
            {"error_code": code, "summary": safe_summary, "status": status.value},
        )
