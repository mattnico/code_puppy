"""Small rendering helpers kept separate from storage and model prompts."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel


def render_allowlisted_state(
    state: BaseModel,
    fields: tuple[str, ...],
    *,
    max_chars: int,
) -> str:
    """Render only named fields from a state model as labeled data."""

    values: dict[str, Any] = {}
    dumped = state.model_dump(mode="json")
    for field in fields:
        if field in dumped:
            values[field] = dumped[field]
    content = "DATA (untrusted): " + json.dumps(
        values, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if len(content) <= max_chars:
        return content
    marker = "\n[allowlisted state truncated]"
    return content[: max(0, max_chars - len(marker))] + marker
