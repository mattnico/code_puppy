import asyncio
from datetime import datetime, timezone

import pytest

from code_puppy.plugins.native_agents.contracts import ExecutionIdentity
from code_puppy.plugins.native_agents.errors import (
    NativeContractError,
    NoActiveExecutionError,
)
from code_puppy.plugins.native_agents.execution import (
    current_execution,
    execution_scope,
)


def _identity(execution_id: str, parent_execution_id: str | None = None):
    return ExecutionIdentity(
        execution_id=execution_id,
        agent_name="agent",
        method_name="method",
        parent_execution_id=parent_execution_id,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_scope_is_available_and_resets_after_success():
    assert current_execution(required=False) is None
    async with execution_scope(_identity("outer")) as active:
        assert active.execution_id == "outer"
        assert current_execution().execution_id == "outer"
    assert current_execution(required=False) is None


@pytest.mark.asyncio
async def test_scope_resets_after_error_and_cancellation():
    with pytest.raises(RuntimeError):
        async with execution_scope(_identity("error")):
            raise RuntimeError("boom")
    assert current_execution(required=False) is None

    task = asyncio.create_task(_cancelled_scope())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert current_execution(required=False) is None


async def _cancelled_scope():
    async with execution_scope(_identity("cancelled")):
        await asyncio.sleep(60)


@pytest.mark.asyncio
async def test_nested_scope_sets_parent_and_rejects_wrong_parent():
    async with execution_scope(_identity("outer")):
        async with execution_scope(_identity("inner")) as active:
            assert active.parent_execution_id == "outer"
        assert current_execution().execution_id == "outer"
        with pytest.raises(NativeContractError):
            async with execution_scope(_identity("bad", parent_execution_id="other")):
                pass


@pytest.mark.asyncio
async def test_child_task_has_its_own_scope_without_clobbering_parent():
    async with execution_scope(_identity("outer")):
        child = asyncio.create_task(_child_identity())
        assert await child == "child"
        assert current_execution().execution_id == "outer"


async def _child_identity():
    async with execution_scope(_identity("child")):
        await asyncio.sleep(0)
        return current_execution().execution_id


def test_required_lookup_outside_scope_fails():
    with pytest.raises(NoActiveExecutionError):
        current_execution()
