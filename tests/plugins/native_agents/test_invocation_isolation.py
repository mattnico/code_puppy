from __future__ import annotations


import pytest

from code_puppy.agents.base_agent import BaseAgent
from code_puppy.plugins.native_agents.demo_agent import (
    ChangeSummaryInput,
    ChangeSummaryResult,
)
from code_puppy.plugins.native_agents.invocation_agent import NativeInvocationAgent
from code_puppy.plugins.native_agents.method import NativeAgentMixin, native_method


class ParentAgent(NativeAgentMixin, BaseAgent):
    @property
    def name(self):
        return "parent"

    @property
    def display_name(self):
        return "Parent"

    @property
    def description(self):
        return "parent"

    def get_system_prompt(self):
        return "parent instructions"

    def get_available_tools(self):
        return ["read_file"]

    @native_method(
        strategy="predict",
        input_type=ChangeSummaryInput,
        output_type=ChangeSummaryResult,
    )
    async def review(self, request: ChangeSummaryInput) -> ChangeSummaryResult: ...


@pytest.mark.asyncio
async def test_invocation_adapter_owns_mutable_runtime_state():
    parent = ParentAgent()
    parent._message_history = ["keep me"]
    parent._code_generation_agent = object()
    parent.pydantic_agent = object()
    parent._mcp_servers = ["keep mcp"]
    spec = parent.get_native_method("review")
    adapter = NativeInvocationAgent(parent, spec, "method prompt")

    assert adapter.get_available_tools() == ["read_file"]
    assert "method prompt" in adapter.get_full_system_prompt()
    assert adapter._message_history == []
    adapter._message_history.append("isolated")
    adapter._mcp_servers.append("isolated mcp")

    assert parent._message_history == ["keep me"]
    assert parent._code_generation_agent is not None
    assert parent.pydantic_agent is not None
    assert parent._mcp_servers == ["keep mcp"]


def test_parent_without_mixin_has_no_native_registry():
    class Ordinary(BaseAgent):
        @property
        def name(self):
            return "ordinary"

        @property
        def display_name(self):
            return "Ordinary"

        @property
        def description(self):
            return "ordinary"

        def get_system_prompt(self):
            return "ordinary"

        def get_available_tools(self):
            return []

    ordinary = Ordinary()
    assert not hasattr(ordinary, "native_methods")
