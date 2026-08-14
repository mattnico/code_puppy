"""Bounded authored prompt construction for typed prediction."""

from __future__ import annotations

import json

from pydantic import BaseModel

from .contracts import MethodSpec
from .errors import NativeContractError


def _schema_json(model: type[BaseModel], budget: int) -> str:
    try:
        rendered = json.dumps(
            model.model_json_schema(), ensure_ascii=False, sort_keys=True
        )
    except (TypeError, ValueError) as exc:
        raise NativeContractError(
            "native output schema is not JSON-compatible"
        ) from exc
    schema_budget = min(4_000, max(256, budget // 3))
    if len(rendered) > schema_budget:
        raise NativeContractError("native output schema exceeds the context budget")
    return rendered


def build_method_prompt(
    spec: MethodSpec,
    payload: BaseModel,
    *,
    execution_id: str,
    parent_agent_name: str,
    context_text: str = "",
) -> str:
    """Render one deterministic, bounded model prompt."""

    try:
        input_json = json.dumps(
            payload.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise NativeContractError("native input is not JSON-compatible") from exc
    budget = spec.context_budget.max_chars
    if len(input_json) > budget:
        raise NativeContractError("native input exceeds the context budget")
    schema = _schema_json(spec.output_type, budget)
    instructions = spec.instructions.strip()
    instruction_block = f"\nMethod notes:\n{instructions}\n" if instructions else ""
    context_block = (
        f"\n## Current execution context\n{context_text}\n" if context_text else ""
    )
    prompt = (
        f"## Native method: {spec.name}\n"
        "Produce an evidence-based structured result. Return every required "
        "field in the declared output model. If the input is insufficient, "
        "record that in the model's limitations fields instead of inventing facts.\n"
        f"Execution: {execution_id}\n"
        f"Parent agent: {parent_agent_name}\n"
        f"Output schema: {schema}\n"
        f"{instruction_block}{context_block}\n"
        f"## Input\n{input_json}"
    )
    if len(prompt) > budget:
        raise NativeContractError("native method prompt exceeds the context budget")
    return prompt
