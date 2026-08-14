"""Bounded model-visible context assembled from durable native records."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from pydantic import BaseModel

from .contracts import (
    ContextBlock,
    ContextBudget,
    EventSummary,
    MethodSpec,
    NativeContextView,
    NativeExecutionRecord,
    NativeStateEnvelope,
    ReferenceHandle,
)

_SECRET_NAME = re.compile(
    r"(?:token|secret|password|credential|authorization|private[_-]?key)", re.I
)


class ContextRenderer:
    """Render only bounded, explicitly selected execution information."""

    def render(
        self,
        *,
        spec: MethodSpec,
        execution: NativeExecutionRecord,
        state: NativeStateEnvelope | None,
        events: Iterable[EventSummary],
        budget: ContextBudget | None = None,
        references: Iterable[ReferenceHandle] = (),
    ) -> NativeContextView:
        budget = budget or spec.context_budget
        blocks = [
            self._block(
                "native_method_contract",
                100,
                "method="
                + spec.name
                + " strategy="
                + spec.strategy.value
                + " output="
                + spec.output_schema_name
                + " capabilities="
                + (", ".join(spec.allowed_capabilities) or "none"),
                "method",
                budget.max_chars,
            ),
            self._block(
                "execution_status",
                90,
                json.dumps(
                    {
                        "execution_id": execution.execution_id,
                        "status": execution.status.value,
                        "method_version": execution.method_version,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "events",
                budget.max_chars,
            ),
        ]
        if state is not None:
            state_block = self._state_block(state, spec.state_type, budget.max_chars)
            if state_block is not None:
                blocks.append(state_block)
        event_items = list(events)[: budget.max_events]
        if event_items:
            blocks.append(
                self._block(
                    "recent_events",
                    60,
                    "\n".join(self._event_line(event) for event in event_items),
                    "events",
                    budget.max_chars,
                )
            )
        reference_items = list(references)[: budget.max_preview_items]
        if reference_items:
            blocks.append(
                self._block(
                    "references",
                    70,
                    "\n".join(
                        self._reference_line(reference, spec.allowed_capabilities)
                        for reference in reference_items
                    ),
                    "references",
                    budget.max_chars,
                )
            )

        blocks.sort(key=lambda block: (-block.priority, block.name))
        selected: list[ContextBlock] = []
        used = 0
        view_truncated = False
        for block in blocks:
            separator = 2 if selected else 0
            remaining = budget.max_chars - used - separator
            if remaining <= 0:
                view_truncated = True
                break
            rendered_block = _render_block(block)
            content = block.content
            block_truncated = block.truncated
            if len(rendered_block) > remaining:
                marker = "\n[context truncated; retrieve approved bounded data]"
                heading = f"## {block.name}\n"
                content_budget = remaining - len(heading)
                if content_budget <= 0:
                    view_truncated = True
                    break
                if content_budget < len(marker):
                    content = marker[:content_budget]
                else:
                    content = content[: content_budget - len(marker)] + marker
                block_truncated = True
                view_truncated = True
                rendered_block = _render_block(
                    block.model_copy(
                        update={"content": content, "truncated": block_truncated}
                    )
                )
            selected.append(
                block.model_copy(
                    update={"content": content, "truncated": block_truncated}
                )
            )
            used += separator + len(rendered_block)
            if block_truncated:
                break
        if len(selected) < len(blocks):
            view_truncated = True
        return NativeContextView(
            execution_id=execution.execution_id,
            blocks=selected,
            total_chars=used,
            truncated=view_truncated,
        )

    @staticmethod
    def _block(name: str, priority: int, content: str, source: str, max_chars: int):
        marker = "\n[block truncated]"
        truncated = len(content) > max_chars
        if truncated:
            content = content[: max(0, max_chars - len(marker))] + marker
        return ContextBlock(
            name=name,
            priority=priority,
            content=content,
            source=source,
            truncated=truncated,
        )

    @staticmethod
    def _state_block(
        snapshot: NativeStateEnvelope,
        state_type: type[BaseModel] | None,
        max_chars: int,
    ):
        if state_type is None:
            return None
        allowed = getattr(state_type, "__native_context_fields__", ())
        if not isinstance(allowed, tuple):
            return None
        values = {
            name: snapshot.state_json[name]
            for name in allowed
            if name in snapshot.state_json and not _SECRET_NAME.search(name)
        }
        content = (
            "DATA (untrusted) revision="
            + str(snapshot.revision)
            + " "
            + json.dumps(
                values, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        )
        return ContextRenderer._block(
            "typed_state", 80, content, "state", min(max_chars, 4_000)
        )

    @staticmethod
    def _reference_line(
        reference: ReferenceHandle, capabilities: tuple[str, ...]
    ) -> str:
        operations = [
            name
            for name in capabilities
            if name.startswith(reference.resource_type + ".")
        ]
        allowed = ", ".join(operations) if operations else "none"
        return (
            f"- {reference.preview.title!r}: {(reference.preview.count or 0):,} "
            f"items; sample={len(reference.preview.sample)}. "
            f"Allowed operations: {allowed}. This is a bounded preview, not the full data set."
        )

    @staticmethod
    def _event_line(event: EventSummary) -> str:
        return f"{event.sequence}: {event.kind.value} — {event.summary}"


def _render_block(block: ContextBlock) -> str:
    return f"## {block.name}\n{block.content}"


def render_context_text(view: NativeContextView) -> str:
    """Render a view without exposing raw storage structure."""

    return "\n\n".join(_render_block(block) for block in view.blocks)
