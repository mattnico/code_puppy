from code_puppy.plugins.native_agents import registry
from code_puppy.plugins.native_agents.demo_agent import (
    ChangeSummaryResult,
    Finding,
    NativeReviewerAgent,
)


def test_demo_agent_is_not_registered_when_disabled(monkeypatch):
    monkeypatch.setattr(registry, "is_enabled", lambda: False)
    assert registry.registered_agents() == []


def test_demo_agent_registration_is_explicit_and_consumable(monkeypatch):
    monkeypatch.setattr(registry, "is_enabled", lambda: True)
    entries = registry.registered_agents()
    assert [entry["name"] for entry in entries] == ["native-reviewer"]

    agent = NativeReviewerAgent()
    result = ChangeSummaryResult(
        summary="summary",
        findings=[
            Finding(severity="error", message="bad"),
            Finding(severity="warning", message="check"),
            Finding(severity="error", message="worse"),
        ],
        confidence="medium",
        limitations=[],
    )
    grouped = agent.findings_by_severity(result)
    assert [finding.message for finding in grouped["error"]] == ["bad", "worse"]
    assert [finding.message for finding in grouped["warning"]] == ["check"]
    assert grouped["info"] == []
