"""Opt-in native-agent catalogue integration."""

from __future__ import annotations

from typing import Any

from .config import is_enabled
from .demo_agent import NativeReviewerAgent


def registered_agents() -> list[dict[str, Any]]:
    """Return only the deliberately selected demo agent when enabled."""

    if not is_enabled():
        return []
    return [{"name": "native-reviewer", "class": NativeReviewerAgent}]
