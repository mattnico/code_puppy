"""Integration notes/helpers for the existing Pydantic AI runtime."""

from __future__ import annotations

from typing import Any

from code_puppy.agents._builder import build_pydantic_agent

from ..contracts import MethodSpec
from ..invocation_agent import NativeInvocationAgent


def build_isolated_invocation(parent_agent: Any, spec: MethodSpec, execution_id: str):
    """Use Code Puppy's builder; never create a parallel provider stack."""

    adapter = NativeInvocationAgent(parent_agent, spec, spec.instructions)
    built = build_pydantic_agent(
        adapter,
        output_type=spec.output_type,
        message_group=execution_id,
        retries=spec.max_validation_repairs,
    )
    return adapter, built
