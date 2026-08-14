import pytest
from pydantic import ValidationError

from code_puppy.plugins.native_agents.contracts import ContextBudget
from code_puppy.plugins.native_agents.prompts import build_method_prompt
from code_puppy.plugins.native_agents.demo_agent import (
    ChangeSummaryInput,
    NativeReviewerAgent,
)


def test_context_budget_has_hard_bounds():
    with pytest.raises(ValidationError):
        ContextBudget(max_chars=255, max_events=1, max_preview_items=0)
    with pytest.raises(ValidationError):
        ContextBudget(max_chars=100_001, max_events=1, max_preview_items=0)


def test_prompt_rejects_input_that_exceeds_declared_budget():
    spec = NativeReviewerAgent().get_native_method("summarize_change")
    spec = spec.model_copy(
        update={
            "context_budget": ContextBudget(
                max_chars=256,
                max_events=1,
                max_preview_items=0,
            )
        }
    )
    with pytest.raises(Exception):
        build_method_prompt(
            spec,
            ChangeSummaryInput(request="x" * 200, diff_text="y" * 200),
            execution_id="exec",
            parent_agent_name="agent",
        )
