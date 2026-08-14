from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from code_puppy.plugins.native_agents.capability_adapters.search_results import (
    RESOURCE_TYPE,
    _count_by_path,
    _filter_prefix,
    _page,
    _sample,
    preview_for,
    register_search_result_capabilities,
)
from code_puppy.plugins.native_agents.capabilities import CapabilityRegistry
from code_puppy.plugins.native_agents.contracts import (
    ExecutionIdentity,
    SearchCountRequest,
    SearchMatch,
    SearchPageRequest,
    SearchPrefixRequest,
    SearchResultSet,
    SearchSampleRequest,
    SearchHandleResult,
    NativeStrategyName,
)
from code_puppy.plugins.native_agents.events import EventStore
from code_puppy.plugins.native_agents.reference_store import ReferenceStore
from code_puppy.plugins.native_agents.state_store import StateStore


def _resource():
    return SearchResultSet(
        query="needle",
        source_root_label="repo",
        matches=[
            SearchMatch(path="src/a.py", line_number=1, snippet="needle"),
            SearchMatch(path="src/b.py", line_number=2, snippet="needle"),
            SearchMatch(path="tests/a.py", line_number=3, snippet="needle"),
        ],
    )


def _execution():
    return ExecutionIdentity(
        execution_id="exec-search",
        agent_name="agent",
        method_name="method",
        session_id="session",
        created_at=datetime.now(timezone.utc),
    )


def test_search_adapter_enforces_bounded_operations_and_safe_prefixes():
    resource = _resource()
    execution = _execution()
    assert _page(resource, SearchPageRequest(offset=1, limit=1), execution).total == 3
    assert (
        _count_by_path(resource, SearchCountRequest(max_groups=100), execution)
        .groups[0]
        .prefix
        == "src"
    )
    assert (
        _sample(resource, SearchSampleRequest(seed=1, limit=2), execution)
        .matches[0]
        .path
        == "src/b.py"
    )
    assert len(preview_for(resource).sample) == 3
    assert (
        len(
            _filter_prefix(
                resource, SearchPrefixRequest(prefix="src"), execution
            ).matches
        )
        == 2
    )
    with pytest.raises(ValueError):
        _filter_prefix(resource, SearchPrefixRequest(prefix="src/../tests"), execution)
    with pytest.raises(ValueError):
        _filter_prefix(resource, SearchPrefixRequest(prefix="C:/repo"), execution)
    with pytest.raises(ValidationError):
        SearchPageRequest(limit=51)
    with pytest.raises(ValidationError):
        SearchMatch(path="../outside", snippet="unsafe")
    with pytest.raises(ValidationError):
        SearchMatch(path="/absolute/path", snippet="unsafe")


@pytest.mark.asyncio
async def test_filter_capability_creates_derived_scoped_handle(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "code_puppy.plugins.native_agents.capability_policy.is_enabled", lambda: True
    )
    path = tmp_path / "native.sqlite3"
    state = StateStore(str(path))
    execution = _execution()
    state.create_execution(
        execution, method_version=1, strategy=NativeStrategyName.PREDICT
    )
    events = EventStore(str(path))
    references = ReferenceStore(event_store=events)
    registry = CapabilityRegistry(references=references, event_store=events)
    register_search_result_capabilities(registry, references)
    handle = await references.create(
        resource_type=RESOURCE_TYPE,
        value=_resource(),
        preview=preview_for(_resource()),
        execution=execution,
    )
    from code_puppy.plugins.native_agents.contracts import MethodSpec

    method = MethodSpec(
        name="method",
        strategy=NativeStrategyName.PREDICT,
        input_schema_name="SearchPrefixRequest",
        output_schema_name="SearchHandleResult",
        input_type=SearchPrefixRequest,
        output_type=SearchHandleResult,
        allowed_capabilities=("search_results.filter_path_prefix",),
    )
    result = await registry.invoke(
        "search_results.filter_path_prefix",
        handle,
        SearchPrefixRequest(prefix="src"),
        method=method,
        execution=execution,
    )
    assert result.handle.preview.count == 2
    assert await references.resolve(
        result.handle, execution=execution, expected_type=RESOURCE_TYPE
    )
