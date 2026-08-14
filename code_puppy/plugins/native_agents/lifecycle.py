"""Explicit terminal lifecycle helpers for native executions."""

from __future__ import annotations

from .contracts import NativeExecutionStatus
from .errors import NativeContractError

_ALLOWED_TRANSITIONS = {
    NativeExecutionStatus.CREATED: frozenset(
        {
            NativeExecutionStatus.CREATED,
            NativeExecutionStatus.RUNNING,
            NativeExecutionStatus.FAILED,
            NativeExecutionStatus.CANCELLED,
        }
    ),
    NativeExecutionStatus.RUNNING: frozenset(
        {
            NativeExecutionStatus.RUNNING,
            NativeExecutionStatus.FINISHED,
            NativeExecutionStatus.FAILED,
            NativeExecutionStatus.CANCELLED,
        }
    ),
    NativeExecutionStatus.FINISHED: frozenset({NativeExecutionStatus.FINISHED}),
    NativeExecutionStatus.FAILED: frozenset({NativeExecutionStatus.FAILED}),
    NativeExecutionStatus.CANCELLED: frozenset({NativeExecutionStatus.CANCELLED}),
}


def validate_transition(
    current: NativeExecutionStatus, requested: NativeExecutionStatus
) -> None:
    """Reject transitions that could make a durable record look successful."""

    if requested not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise NativeContractError(
            f"invalid native execution transition: {current.value} -> {requested.value}"
        )
