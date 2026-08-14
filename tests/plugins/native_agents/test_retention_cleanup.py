from datetime import datetime, timedelta, timezone

from code_puppy.plugins.native_agents.contracts import (
    ExecutionIdentity,
    NativeExecutionStatus,
    NativeStrategyName,
)
from code_puppy.plugins.native_agents.state_store import StateStore


def _identity(execution_id, created_at):
    return ExecutionIdentity(
        execution_id=execution_id,
        agent_name="agent",
        method_name="method",
        created_at=created_at,
    )


def test_cleanup_removes_old_terminal_records_but_preserves_active(tmp_path):
    store = StateStore(str(tmp_path / "native.sqlite3"))
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=31)
    store.create_execution(
        _identity("finished", old),
        method_version=1,
        strategy=NativeStrategyName.PREDICT,
    )
    store.set_execution_status("finished", NativeExecutionStatus.RUNNING)
    store.set_execution_status("finished", NativeExecutionStatus.FINISHED)
    store.create_execution(
        _identity("active", old), method_version=1, strategy=NativeStrategyName.PREDICT
    )
    store.set_execution_status("active", NativeExecutionStatus.RUNNING)
    assert store.purge_expired(30, now=now) == 1
    assert store.get_execution("finished") is None
    assert store.get_execution("active").status is NativeExecutionStatus.RUNNING
