"""Read-only capabilities for a bounded repository search result set."""

from __future__ import annotations

import posixpath
from collections import Counter

from ..contracts import (
    CapabilityEffect,
    ExecutionIdentity,
    SearchCountRequest,
    SearchCounts,
    SearchPage,
    SearchPageRequest,
    SearchPathCount,
    SearchPrefixRequest,
    SearchResultSet,
    SearchSampleRequest,
    SearchHandleResult,
)
from ..reference_store import ReferenceStore

RESOURCE_TYPE = "search_results"


def preview_for(resource: SearchResultSet):
    from ..contracts import ReferencePreview

    sample = [match.model_dump(mode="json") for match in resource.matches[:5]]
    return ReferencePreview(
        title=f"Search results for {resource.query}",
        count=len(resource.matches),
        summary=f"{len(resource.matches)} matches under {resource.source_root_label}",
        sample=sample,
        truncated=len(resource.matches) > len(sample),
    )


def _page(
    resource: SearchResultSet, request: SearchPageRequest, _execution: ExecutionIdentity
):
    start = request.offset
    return SearchPage(
        offset=start,
        matches=resource.matches[start : start + request.limit],
        total=len(resource.matches),
    )


def _filter_prefix(
    resource: SearchResultSet,
    request: SearchPrefixRequest,
    _execution: ExecutionIdentity,
):
    prefix = _normalized_prefix(request.prefix)
    return SearchResultSet(
        query=resource.query,
        matches=[
            match
            for match in resource.matches
            if _normalized_match_path(match.path).startswith(prefix)
        ],
        source_root_label=resource.source_root_label,
    )


def _count_by_path(
    resource: SearchResultSet,
    request: SearchCountRequest,
    _execution: ExecutionIdentity,
):
    counts = Counter(_first_component(match.path) for match in resource.matches)
    groups = [
        SearchPathCount(prefix=prefix, count=count)
        for prefix, count in sorted(
            counts.items(), key=lambda item: (-item[1], item[0])
        )[: request.max_groups]
    ]
    return SearchCounts(groups=groups)


def _sample(
    resource: SearchResultSet,
    request: SearchSampleRequest,
    _execution: ExecutionIdentity,
):
    # A deterministic stride avoids depending on process-randomized hash state.
    if not resource.matches:
        return SearchPage(offset=0, matches=[], total=0)
    start = request.seed % len(resource.matches)
    selected = [
        resource.matches[(start + index) % len(resource.matches)]
        for index in range(min(request.limit, len(resource.matches)))
    ]
    return SearchPage(offset=start, matches=selected, total=len(resource.matches))


def _normalized_prefix(prefix: str) -> str:
    candidate = prefix.replace("\\", "/")
    _validate_relative_path(candidate)
    normalized = posixpath.normpath(candidate)
    if normalized in {".", ""}:
        raise ValueError("path prefix must not be empty")
    return normalized.rstrip("/") + "/"


def _normalized_match_path(path: str) -> str:
    candidate = path.replace("\\", "/")
    _validate_relative_path(candidate)
    normalized = posixpath.normpath(candidate)
    if normalized in {".", ""}:
        raise ValueError("search match path must not be empty")
    return normalized.rstrip("/") + "/"


def _validate_relative_path(path: str) -> None:
    if (
        not path
        or path.startswith("/")
        or path.startswith("~")
        or (len(path) >= 2 and path[1] == ":")
    ):
        raise ValueError("path must be relative")
    if any(component == ".." for component in path.split("/")):
        raise ValueError("path must remain within the result root")


def _first_component(path: str) -> str:
    return _normalized_match_path(path).split("/", 1)[0]


def register_search_result_capabilities(registry, references: ReferenceStore) -> None:
    """Register only explicit, read-only search-result operations."""

    from ..contracts import CapabilitySpec

    async def filter_prefix_handle(
        resource: SearchResultSet,
        request: SearchPrefixRequest,
        execution: ExecutionIdentity,
    ) -> SearchHandleResult:
        filtered = _filter_prefix(resource, request, execution)
        handle = await references.create(
            resource_type=RESOURCE_TYPE,
            value=filtered,
            preview=preview_for(filtered),
            execution=execution,
        )
        return SearchHandleResult(handle=handle)

    registry.register(
        CapabilitySpec(
            name="search_results.page",
            resource_type=RESOURCE_TYPE,
            effect=CapabilityEffect.OBSERVE,
            input_model=SearchPageRequest,
            output_model=SearchPage,
            description="Return one bounded page of search matches.",
        ),
        _page,
    )
    registry.register(
        CapabilitySpec(
            name="search_results.filter_path_prefix",
            resource_type=RESOURCE_TYPE,
            effect=CapabilityEffect.COMPUTE,
            input_model=SearchPrefixRequest,
            output_model=SearchHandleResult,
            description="Create a filtered result set by relative path prefix.",
        ),
        filter_prefix_handle,
    )
    registry.register(
        CapabilitySpec(
            name="search_results.count_by_path",
            resource_type=RESOURCE_TYPE,
            effect=CapabilityEffect.COMPUTE,
            input_model=SearchCountRequest,
            output_model=SearchCounts,
            description="Count matches by first relative path component.",
        ),
        _count_by_path,
    )
    registry.register(
        CapabilitySpec(
            name="search_results.sample",
            resource_type=RESOURCE_TYPE,
            effect=CapabilityEffect.OBSERVE,
            input_model=SearchSampleRequest,
            output_model=SearchPage,
            description="Return a deterministic bounded sample of matches.",
        ),
        _sample,
    )
