from datetime import timedelta

import pytest

from code_puppy.plugins.native_agents.contracts import (
    ExecutionIdentity,
    ReferencePreview,
)
from code_puppy.plugins.native_agents.errors import HandleUnavailableError
from code_puppy.plugins.native_agents.reference_store import ReferenceStore


def _execution(name: str, session: str = "session"):
    from datetime import datetime, timezone

    return ExecutionIdentity(
        execution_id=name,
        agent_name="agent",
        method_name="method",
        session_id=session,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_handles_are_opaque_scoped_and_expiring():
    store = ReferenceStore(default_ttl=timedelta(minutes=1))
    execution = _execution("exec-a")
    handle = await store.create(
        resource_type="search_results",
        value={"secret": "live object"},
        preview=ReferencePreview(
            title="results", count=1, summary="one result", sample=[]
        ),
        execution=execution,
    )
    assert len(handle.handle_id) >= 20
    assert handle.handle_id != "1"
    assert await store.resolve(
        handle, execution=execution, expected_type="search_results"
    ) == {"secret": "live object"}

    with pytest.raises(HandleUnavailableError):
        await store.resolve(
            handle,
            execution=_execution("exec-b"),
            expected_type="search_results",
        )
    with pytest.raises(HandleUnavailableError):
        await store.resolve(
            handle,
            execution=execution,
            expected_type="other_resource",
        )

    expired_store = ReferenceStore(default_ttl=timedelta(microseconds=1))
    expired = await expired_store.create(
        resource_type="search_results",
        value=object(),
        preview=ReferencePreview(title="expired", summary="expired"),
        execution=execution,
    )
    import asyncio

    await asyncio.sleep(0.01)
    with pytest.raises(HandleUnavailableError):
        await expired_store.resolve(
            expired, execution=execution, expected_type="search_results"
        )


@pytest.mark.asyncio
async def test_revoke_and_purge_release_live_values():
    store = ReferenceStore()
    execution = _execution("exec-a")
    handle = await store.create(
        resource_type="search_results",
        value=[1, 2, 3],
        preview=ReferencePreview(title="results", summary="three"),
        execution=execution,
    )
    await store.revoke_execution(execution.execution_id)
    with pytest.raises(HandleUnavailableError):
        await store.resolve(handle, execution=execution, expected_type="search_results")
    assert await store.purge_expired() == 0
