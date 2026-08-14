"""Permission integration for future host-side effectful capabilities."""

from __future__ import annotations

from typing import Any

from code_puppy.callbacks import (
    on_file_permission,
    on_pre_tool_call,
    on_run_shell_command,
)


async def observe_tool_call(
    tool_name: str, tool_args: dict[str, Any], context: Any = None
):
    """Route observation through the existing pre-tool callback seam."""

    return await on_pre_tool_call(tool_name, tool_args, context)


def file_allowed(context: Any, file_path: str, operation: str) -> bool:
    """Use Code Puppy's authoritative file permission hook."""

    results = on_file_permission(context, file_path, operation)
    return not any(result is False for result in results)


async def shell_allowed(context: Any, command: str, cwd: str | None = None):
    """Use shell policy only for trusted host paths; CodeAct has no shell path."""

    return await on_run_shell_command(context, command, cwd, 60)
