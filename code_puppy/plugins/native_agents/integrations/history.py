"""Compaction-safe bridge: re-render durable context, never replay a transcript."""

from __future__ import annotations

from collections.abc import Callable

from ..contracts import NativeContextView


class NativeHistoryContext:
    """Fresh context provider for retries/follow-up calls after compaction."""

    def __init__(self, render: Callable[[], NativeContextView]) -> None:
        self._render = render

    def before_native_follow_up(self) -> NativeContextView:
        """Render current state/events instead of reading compacted history."""

        return self._render()
