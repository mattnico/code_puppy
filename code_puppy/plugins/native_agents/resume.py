"""Inspection-only resume eligibility; never auto-replays model work."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field

from .config import store_retention_days
from .contracts import MethodSpec, NativeExecutionStatus
from .state_store import StateStore


class ResumeMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    execution_id: str
    status: NativeExecutionStatus
    eligible: bool
    reason: str = Field(max_length=200)
    state_revision: int | None = None


def inspect_resume(
    store: StateStore,
    execution_id: str,
    spec: MethodSpec,
    *,
    now: datetime | None = None,
    retention_days: int | None = None,
) -> ResumeMetadata:
    """Report whether an interrupted execution may be inspected/resumed later."""

    record = store.get_execution(execution_id)
    if record is None:
        return ResumeMetadata(
            execution_id=execution_id,
            status=NativeExecutionStatus.FAILED,
            eligible=False,
            reason="execution_not_found",
        )
    now = now or datetime.now(timezone.utc)
    retention = retention_days or store_retention_days()
    expired = now - record.created_at > timedelta(days=retention)
    state = store.get_state(execution_id)
    if expired:
        reason = "retention_expired"
        eligible = False
    elif record.method_version != spec.version:
        reason = "method_version_changed"
        eligible = False
    elif record.status is NativeExecutionStatus.RUNNING:
        reason = "interrupted_execution_requires_explicit_resume"
        eligible = True
    else:
        reason = "terminal_execution_is_inspection_only"
        eligible = False
    return ResumeMetadata(
        execution_id=execution_id,
        status=record.status,
        eligible=eligible,
        reason=reason,
        state_revision=state.revision if state else None,
    )
