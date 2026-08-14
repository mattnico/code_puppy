"""Session boundary: correlate metadata, never merge native state into history."""

from __future__ import annotations

from typing import Any


def session_id(agent: Any) -> str | None:
    value = getattr(agent, "session_id", None)
    return value if isinstance(value, str) and value else None


def restore_policy() -> dict[str, bool]:
    """Normal session restore never revives process-local handles or runs."""

    return {"inspect_execution_metadata": True, "resume_native_execution": False}
