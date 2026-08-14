"""Execution-scoped identity for native methods."""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator

from .contracts import ExecutionIdentity, MethodSpec
from .errors import NativeContractError, NoActiveExecutionError

if TYPE_CHECKING:
    from .capabilities import CapabilityRegistry
    from .context import ContextRenderer
    from .events import EventService
    from .reference_store import ReferenceStore
    from .state import StateService


@dataclass(frozen=True, slots=True)
class NativeExecutionContext:
    """Trusted host context for one execution; never persisted or model-visible."""

    identity: ExecutionIdentity
    method: MethodSpec
    state: "StateService | None"
    events: "EventService"
    context: "ContextRenderer"
    references: "ReferenceStore"
    capabilities: "CapabilityRegistry"

    @property
    def execution_id(self) -> str:
        return self.identity.execution_id

    @property
    def agent_name(self) -> str:
        return self.identity.agent_name

    @property
    def session_id(self) -> str | None:
        return self.identity.session_id


_CURRENT_EXECUTION: ContextVar[ExecutionIdentity | None] = ContextVar(
    "native_current_execution", default=None
)


@asynccontextmanager
async def execution_scope(
    identity: ExecutionIdentity,
) -> AsyncIterator[ExecutionIdentity]:
    """Bind *identity* for this task and reset it under every exit path."""

    parent = _CURRENT_EXECUTION.get()
    effective = identity
    if parent is not None:
        if identity.parent_execution_id not in (None, parent.execution_id):
            raise NativeContractError(
                "nested native execution has an unrelated parent execution"
            )
        if identity.parent_execution_id is None:
            effective = identity.model_copy(
                update={"parent_execution_id": parent.execution_id}
            )
    token = _CURRENT_EXECUTION.set(effective)
    try:
        yield effective
    finally:
        _CURRENT_EXECUTION.reset(token)


def current_execution(*, required: bool = True) -> ExecutionIdentity | None:
    """Return the current identity, optionally requiring an active scope."""

    identity = _CURRENT_EXECUTION.get()
    if identity is None and required:
        raise NoActiveExecutionError("no native execution is active")
    return identity


def clear_execution_scope_for_tests() -> None:
    """Reset the current task-local value for isolated tests."""

    _CURRENT_EXECUTION.set(None)
