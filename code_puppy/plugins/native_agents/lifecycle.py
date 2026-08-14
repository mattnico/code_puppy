"""Explicit terminal lifecycle helpers for native executions."""

from __future__ import annotations

from .contracts import NativeExecutionStatus
from .errors import NativeContractError

_TERMINAL = frozenset(
    {
        NativeExecutionStatus.FINISHED,
        NativeExecutionStatus.FAILED,
        NativeExecutionStatus.CANCELLED,
    }
)


def validate_transition(
    current: NativeExecutionStatus, requested: NativeExecutionStatus
) -> None:
    """Reject transitions that could make a durable record look successful."""

    if current in _TERMINAL and requested is not current:
        raise NativeContractError("terminal native executions cannot change status")
    if (
        requested is NativeExecutionStatus.FINISHED
        and current is not NativeExecutionStatus.RUNNING
    ):
        raise NativeContractError("only running native executions can finish")
