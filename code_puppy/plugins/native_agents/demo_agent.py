"""Small opt-in native reviewer proving structured downstream results."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from code_puppy.agents.base_agent import BaseAgent
from code_puppy.i18n import t

from .method import NativeAgentMixin, native_method


class ChangeSummaryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request: str = Field(min_length=1, max_length=8_000)
    diff_text: str = Field(min_length=1, max_length=40_000)


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    severity: Literal["info", "warning", "error"]
    message: str = Field(min_length=1, max_length=4_000)
    file_path: str | None = Field(default=None, max_length=1_000)


class ChangeSummaryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: str = Field(min_length=1, max_length=8_000)
    findings: list[Finding] = Field(max_length=100)
    confidence: Literal["low", "medium", "high"]
    limitations: list[str] = Field(max_length=30)


class NativeReviewerAgent(NativeAgentMixin, BaseAgent):
    """An explicitly selected, read-only structured change reviewer."""

    @property
    def name(self) -> str:
        return "native-reviewer"

    @property
    def display_name(self) -> str:
        return t("native_agents.demo.display_name")

    @property
    def description(self) -> str:
        return t("native_agents.demo.description")

    def get_system_prompt(self) -> str:
        return (
            "Review only the supplied change request and diff. Do not claim to "
            "have inspected files or run commands. Keep findings evidence-based."
        )

    def get_available_tools(self) -> list[str]:
        return []

    @native_method(
        strategy="predict",
        input_type=ChangeSummaryInput,
        output_type=ChangeSummaryResult,
        max_validation_repairs=1,
    )
    async def summarize_change(
        self, request: ChangeSummaryInput
    ) -> ChangeSummaryResult:
        """Produce structured findings from the supplied change text."""

        ...

    def findings_by_severity(
        self, result: ChangeSummaryResult
    ) -> dict[str, list[Finding]]:
        """Show normal Python consumption without reparsing model prose."""

        grouped: dict[str, list[Finding]] = {"info": [], "warning": [], "error": []}
        for finding in result.findings:
            grouped[finding.severity].append(finding)
        return grouped
