from datetime import datetime, timedelta, timezone

from code_puppy.plugins.native_agents.contracts import (
    ExecutionIdentity,
    MethodSpec,
    NativeExecutionStatus,
    NativeStrategyName,
)
from code_puppy.plugins.native_agents.resume import inspect_resume
from code_puppy.plugins.native_agents.state_store import StateStore


def _spec(version=1):
    from pydantic import BaseModel

    return MethodSpec(
        name="method",
        strategy=NativeStrategyName.PREDICT,
        input_schema_name="Input",
        output_schema_name="Output",
        input_type=BaseModel,
        output_type=BaseModel,
        version=version,
    )


def test_resume_metadata_is_inspection_only_and_version_aware(tmp_path):
    path = tmp_path / "native.sqlite3"
    store = StateStore(str(path))
    created = datetime.now(timezone.utc)
    identity = ExecutionIdentity(
        execution_id="exec-resume",
        agent_name="agent",
        method_name="method",
        created_at=created,
    )
    store.create_execution(
        identity, method_version=1, strategy=NativeStrategyName.PREDICT
    )
    store.set_execution_status(identity.execution_id, NativeExecutionStatus.RUNNING)
    metadata = inspect_resume(store, identity.execution_id, _spec(), now=created)
    assert metadata.eligible is True
    assert metadata.reason == "interrupted_execution_requires_explicit_resume"

    incompatible = inspect_resume(
        store, identity.execution_id, _spec(version=2), now=created
    )
    assert incompatible.eligible is False
    assert incompatible.reason == "method_version_changed"

    expired = inspect_resume(
        store,
        identity.execution_id,
        _spec(),
        now=created + timedelta(days=31),
        retention_days=30,
    )
    assert expired.eligible is False
    assert expired.reason == "retention_expired"
