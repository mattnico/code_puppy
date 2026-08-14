"""Opt-in bounded Puppy Kennel recall; native state remains authoritative."""

from __future__ import annotations


def build_recall_block() -> str | None:
    """Call the existing Kennel retriever without creating a second store."""

    try:
        from code_puppy.plugins.puppy_kennel.retriever import (
            build_recall_block as recall,
        )

        return recall()
    except Exception:
        return None


def bounded_recall(*, enabled: bool, max_chars: int = 1_500) -> str | None:
    """Return Kennel's existing packed recall only when the method opts in."""

    if not enabled or max_chars < 1:
        return None
    try:
        block = build_recall_block()
    except Exception:
        return None
    if not block:
        return None
    return block[:max_chars]


def curated_write(*, content: str, explicit: bool) -> bool:
    """Never write memory implicitly; this adapter only acknowledges consent."""

    if not explicit or not content.strip():
        return False
    return False
