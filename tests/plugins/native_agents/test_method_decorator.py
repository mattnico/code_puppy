from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from code_puppy.plugins.native_agents.contracts import ContextBudget, NativeStrategyName
from code_puppy.plugins.native_agents.errors import (
    NativeContractError,
    NativeRuntimeDisabledError,
)
from code_puppy.plugins.native_agents.method import (
    SPEC_ATTRIBUTE,
    NativeAgentMixin,
    native_method,
)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    value: str


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    result: str


class NativeTestAgent(NativeAgentMixin):
    @native_method(
        strategy="predict",
        input_type=Input,
        output_type=Output,
        context_budget=ContextBudget(max_chars=256, max_events=1, max_preview_items=0),
    )
    async def review(self, request: Input) -> Output:
        """Review the supplied value."""

        ...


def test_decorator_preserves_metadata_and_attaches_frozen_spec():
    method = NativeTestAgent.review
    spec = getattr(method, SPEC_ATTRIBUTE)
    assert method.__name__ == "review"
    assert method.__doc__ == "Review the supplied value."
    assert spec.name == "review"
    assert spec.strategy is NativeStrategyName.PREDICT
    with pytest.raises(ValueError):
        spec.name = "changed"


def test_registry_includes_inherited_methods_and_lookup_by_identifier():
    agent = NativeTestAgent()
    assert list(agent.native_methods()) == ["review"]
    assert agent.get_native_method("review").output_type is Output


def test_wrong_input_is_rejected_before_runtime():
    agent = NativeTestAgent()
    with pytest.raises(NativeContractError):
        import asyncio

        asyncio.run(agent.review("not-an-input"))  # type: ignore[arg-type]


def test_missing_runtime_is_disabled():
    agent = NativeTestAgent()
    with pytest.raises(NativeRuntimeDisabledError):
        import asyncio

        asyncio.run(agent.review(Input(value="x")))


def test_declarations_reject_non_async_or_non_stub_functions():
    def sync(self, request: Input) -> Output:
        return Output(result=request.value)

    with pytest.raises(NativeContractError):
        native_method(strategy="predict", input_type=Input, output_type=Output)(sync)


def test_declarations_reject_codeact_and_capabilities():
    with pytest.raises(NativeContractError):
        native_method(strategy="codeact", input_type=Input, output_type=Output)

    async def declared(self, request: Input) -> Output: ...

    with pytest.raises(NativeContractError):
        native_method(
            strategy="predict",
            input_type=Input,
            output_type=Output,
            capabilities=("read",),
        )(declared)
