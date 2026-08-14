from __future__ import annotations

from pydantic_ai.models.test import TestModel

from code_puppy.plugins.native_agents.demo_agent import (
    ChangeSummaryInput,
    ChangeSummaryResult,
    NativeReviewerAgent,
)
from code_puppy.plugins.native_agents.predict import PredictStrategy


async def test_predict_strategy_returns_declared_output_through_real_builder(
    monkeypatch,
):
    monkeypatch.setattr(
        "code_puppy.plugins.native_agents.predict.is_enabled", lambda: True
    )
    import code_puppy.agents._builder as builder

    monkeypatch.setattr(
        builder,
        "load_model_with_fallback",
        lambda *args, **kwargs: (
            TestModel(
                custom_output_args={
                    "summary": "Looks good",
                    "findings": [
                        {
                            "severity": "warning",
                            "message": "Check the boundary",
                            "file_path": "example.py",
                        }
                    ],
                    "confidence": "medium",
                    "limitations": [],
                }
            ),
            "test",
        ),
    )
    monkeypatch.setattr(builder, "load_mcp_servers", lambda **kwargs: [])
    monkeypatch.setattr(builder.ModelFactory, "load_config", lambda: {})

    agent = NativeReviewerAgent()
    result = await PredictStrategy().execute(
        agent,
        agent.get_native_method("summarize_change"),
        ChangeSummaryInput(request="review", diff_text="+ value"),
        execution_id="exec-test",
    )
    assert isinstance(result, ChangeSummaryResult)
    assert result.findings[0].severity == "warning"
    assert result.findings[0].file_path == "example.py"
    assert agent.get_message_history() == []
    assert agent.pydantic_agent is None
    assert agent._code_generation_agent is None


async def test_predict_strategy_rejects_wrong_fake_output(monkeypatch):
    monkeypatch.setattr(
        "code_puppy.plugins.native_agents.predict.is_enabled", lambda: True
    )
    import code_puppy.plugins.native_agents.predict as predict

    class FakeResult:
        output = {"not": "the declared model"}

    class FakePydanticAgent:
        async def run(self, *args, **kwargs):
            return FakeResult()

    monkeypatch.setattr(
        predict, "build_pydantic_agent", lambda *args, **kwargs: FakePydanticAgent()
    )
    monkeypatch.setattr(
        predict, "on_agent_run_start", lambda *args: _empty_async_list()
    )
    monkeypatch.setattr(
        predict, "on_agent_run_end", lambda *args, **kwargs: _empty_async_list()
    )
    monkeypatch.setattr(predict, "on_agent_run_context", lambda *args: [])

    agent = NativeReviewerAgent()
    try:
        await PredictStrategy().execute(
            agent,
            agent.get_native_method("summarize_change"),
            ChangeSummaryInput(request="review", diff_text="+ value"),
            execution_id="exec-test",
        )
    except Exception as exc:
        assert exc.code == "native_output_validation_failed"


async def _empty_async_list():
    return []
