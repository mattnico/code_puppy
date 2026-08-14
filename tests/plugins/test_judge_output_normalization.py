from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from code_puppy.plugins.wiggum import judge as judge_module
from code_puppy.plugins.wiggum.judge_config import JudgeConfig


@pytest.mark.asyncio
async def test_unstructured_judge_output_abstains_without_attribute_error():
    async def fake_run(_prompt, **_kwargs):
        return MagicMock(output="not a structured verdict")

    with (
        patch.object(
            judge_module.ModelFactory, "load_config", return_value={"fake-model": {}}
        ),
        patch.object(judge_module.ModelFactory, "get_model", return_value=MagicMock()),
        patch.object(judge_module, "make_model_settings", return_value={}),
        patch.object(
            judge_module,
            "prepare_prompt_for_model",
            return_value=MagicMock(instructions="i", user_prompt="u"),
        ),
        patch.object(
            judge_module,
            "load_agent",
            return_value=MagicMock(get_available_tools=lambda: []),
        ),
        patch("code_puppy.tools.register_tools_for_agent"),
        patch.object(judge_module, "Agent") as agent_class,
    ):
        agent_class.return_value.run = fake_run
        verdict = await judge_module.judge_goal(
            judge_config=JudgeConfig(name="checker", model="fake-model"),
            implementor_agent=MagicMock(name="implementor"),
            goal="g",
            response="r",
            error=None,
            history=[],
        )

    assert verdict.abstained is True
    assert verdict.complete is False
    assert "invalid structured verdict" in verdict.notes
    assert "not a structured verdict" in verdict.raw_response


@pytest.mark.asyncio
async def test_malformed_mapping_is_rejected_as_an_abstention():
    async def fake_run(_prompt, **_kwargs):
        return MagicMock(output={"complete": "not-a-bool"})

    with (
        patch.object(
            judge_module.ModelFactory, "load_config", return_value={"fake-model": {}}
        ),
        patch.object(judge_module.ModelFactory, "get_model", return_value=MagicMock()),
        patch.object(judge_module, "make_model_settings", return_value={}),
        patch.object(
            judge_module,
            "prepare_prompt_for_model",
            return_value=MagicMock(instructions="i", user_prompt="u"),
        ),
        patch.object(
            judge_module,
            "load_agent",
            return_value=MagicMock(get_available_tools=lambda: []),
        ),
        patch("code_puppy.tools.register_tools_for_agent"),
        patch.object(judge_module, "Agent") as agent_class,
    ):
        agent_class.return_value.run = fake_run
        verdict = await judge_module.judge_goal(
            judge_config=JudgeConfig(name="checker", model="fake-model"),
            implementor_agent=MagicMock(name="implementor"),
            goal="g",
            response="r",
            error=None,
            history=[],
        )

    assert verdict.abstained is True
    assert verdict.complete is False
    assert "complete" in verdict.raw_response
