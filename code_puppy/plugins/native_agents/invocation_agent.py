"""Ephemeral isolated adapter used by one native prediction."""

from __future__ import annotations


from code_puppy.agents.base_agent import BaseAgent

from .contracts import MethodSpec
from .execution import NativeExecutionContext


class NativeInvocationAgent(BaseAgent):
    """A disposable ``BaseAgent`` shell that never mutates its parent."""

    def __init__(
        self,
        parent: BaseAgent,
        spec: MethodSpec,
        prompt: str,
        native_context: NativeExecutionContext | None = None,
    ) -> None:
        super().__init__()
        self.parent = parent
        self.method_spec = spec
        self._method_prompt = prompt
        self._parent_agent_name = parent.name
        parent_mcp_servers = getattr(parent, "_mcp_servers", None)
        self._parent_mcp_servers = (
            None
            if getattr(parent, "pydantic_agent", None) is None
            and not parent_mcp_servers
            else list(parent_mcp_servers or ())
        )
        self.native_context = native_context

    @property
    def name(self) -> str:
        # Keep the parent's logical name so tool/MCP bindings remain normal.
        return self.parent.name

    @property
    def display_name(self) -> str:
        return self.parent.display_name

    @property
    def description(self) -> str:
        return self.parent.description

    def get_system_prompt(self) -> str:
        parent_prompt = self.parent.get_system_prompt()
        return f"{parent_prompt}\n\n{self._method_prompt}"

    def get_full_system_prompt(self) -> str:
        return (
            f"{self.parent.get_full_system_prompt()}\n\n"
            f"{self._method_prompt}{self.get_identity_prompt()}"
        )

    def get_available_tools(self) -> list[str]:
        return list(self.parent.get_available_tools())

    def get_tools_config(self):
        """Delegate static tool configuration without sharing mutable state."""

        config = self.parent.get_tools_config()
        return dict(config) if config else None

    def get_user_prompt(self) -> str | None:
        return self.parent.get_user_prompt()

    def get_model_name(self) -> str | None:
        return self.parent.get_model_name()
