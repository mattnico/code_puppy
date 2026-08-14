from datetime import datetime, timezone

from code_puppy.plugins.native_agents.context import (
    ContextRenderer,
    render_context_text,
)
from code_puppy.plugins.native_agents.contracts import (
    ContextBudget,
    ExecutionIdentity,
    MethodSpec,
    NativeExecutionRecord,
    NativeExecutionStatus,
    NativeStrategyName,
    ReferenceHandle,
    ReferencePreview,
)


def test_context_renders_bounded_handle_preview_without_opaque_id():
    execution = ExecutionIdentity(
        execution_id="exec-ref",
        agent_name="agent",
        method_name="method",
        session_id="session",
        created_at=datetime.now(timezone.utc),
    )
    record = NativeExecutionRecord(
        execution_id=execution.execution_id,
        agent_name=execution.agent_name,
        method_name=execution.method_name,
        method_version=1,
        strategy=NativeStrategyName.PREDICT,
        session_id=execution.session_id,
        status=NativeExecutionStatus.RUNNING,
        created_at=execution.created_at,
    )
    handle = ReferenceHandle(
        handle_id="opaque-secret-id-1234567890",
        resource_type="search_results",
        execution_id=execution.execution_id,
        owner_session_id=execution.session_id,
        created_at=execution.created_at,
        expires_at=execution.created_at,
        preview=ReferencePreview(
            title="example search",
            count=1_248,
            summary="many matches",
            sample=[{"path": "src/example.py"}],
            truncated=True,
        ),
    )
    spec = MethodSpec(
        name="method",
        strategy=NativeStrategyName.PREDICT,
        input_schema_name="Input",
        output_schema_name="Output",
        allowed_capabilities=(
            "search_results.page",
            "search_results.sample",
        ),
        context_budget=ContextBudget(
            max_chars=1_000, max_events=0, max_preview_items=1
        ),
    )
    view = ContextRenderer().render(
        spec=spec,
        execution=record,
        state=None,
        events=[],
        references=[handle],
    )
    text = render_context_text(view)
    assert "example search" in text
    assert "1,248" in text
    assert "search_results.page" in text
    assert handle.handle_id not in text
