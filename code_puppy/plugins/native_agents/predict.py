"""The Tier A typed prediction strategy."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from contextlib import AsyncExitStack
from typing import Any

from pydantic_ai import UnexpectedModelBehavior, UsageLimits

from code_puppy.agents._builder import build_pydantic_agent
from code_puppy.callbacks import (
    on_agent_run_context,
    on_agent_run_end,
    on_agent_run_start,
)
from code_puppy.config import get_message_limit

from .contracts import MethodSpec
from .errors import NativeOutputValidationError
from .invocation_agent import NativeInvocationAgent
from .integrations.kennel import bounded_recall
from .prompts import build_method_prompt

ValidationReporter = Callable[[int, str], Any]


async def _report_validation(
    reporter: ValidationReporter | None,
    attempt: int,
    error_code: str,
) -> None:
    if reporter is None:
        return
    result = reporter(attempt, error_code)
    if inspect.isawaitable(result):
        await result


class PredictStrategy:
    """Run an isolated Pydantic AI invocation with bounded repair attempts."""

    async def execute(
        self,
        parent_agent: Any,
        spec: MethodSpec,
        payload: Any,
        *,
        execution_id: str,
        context_text: str = "",
        on_validation_failure: ValidationReporter | None = None,
    ) -> Any:
        memory = bounded_recall(enabled=spec.memory_opt_in)
        if memory and memory not in parent_agent.get_full_system_prompt():
            context_text = f"{context_text}\n## Curated memory (data)\n{memory}"
        prompt = build_method_prompt(
            spec,
            payload,
            execution_id=execution_id,
            parent_agent_name=parent_agent.name,
            context_text=context_text,
        )
        invocation_agent = NativeInvocationAgent(parent_agent, spec, prompt)
        pydantic_agent = build_pydantic_agent(
            invocation_agent,
            output_type=spec.output_type,
            message_group=execution_id,
            # Native repair attempts are explicit and therefore observable.
            # Leaving provider retries at zero prevents a hidden second budget.
            retries=0,
        )
        model_name = invocation_agent.get_model_name() or "unknown"
        await on_agent_run_start(parent_agent.name, model_name, execution_id)
        success = False
        error: BaseException | None = None
        attempts = spec.max_validation_repairs + 1
        try:
            run_contexts = on_agent_run_context(
                invocation_agent,
                pydantic_agent,
                execution_id,
                invocation_agent._mcp_servers,
            )
            async with AsyncExitStack() as stack:
                for run_context in run_contexts:
                    await stack.enter_async_context(run_context)
                for attempt in range(1, attempts + 1):
                    attempt_prompt = (
                        prompt
                        if attempt == 1
                        else _repair_prompt(spec, attempt, attempts)
                    )
                    try:
                        result = await pydantic_agent.run(
                            attempt_prompt,
                            message_history=invocation_agent._message_history,
                            usage_limits=UsageLimits(request_limit=get_message_limit()),
                        )
                        output = result.output
                        if not isinstance(output, spec.output_type):
                            raise NativeOutputValidationError(
                                "native output did not satisfy the declared schema"
                            )
                        success = True
                        error = None
                        return output
                    except asyncio.CancelledError as exc:
                        error = exc
                        raise
                    except (
                        UnexpectedModelBehavior,
                        NativeOutputValidationError,
                    ) as exc:
                        error = exc
                        await _report_validation(
                            on_validation_failure,
                            attempt,
                            "native_output_validation_failed",
                        )
                        if attempt == attempts:
                            raise NativeOutputValidationError(
                                "native output did not satisfy the declared schema"
                            ) from exc
        except Exception as exc:
            error = exc
            raise
        finally:
            await on_agent_run_end(
                parent_agent.name,
                model_name,
                execution_id,
                success=success,
                error=error if isinstance(error, Exception) else None,
            )


def _repair_prompt(spec: MethodSpec, attempt: int, attempts: int) -> str:
    """Ask for the same contract without echoing provider/model data."""

    return (
        f"## Native method repair attempt {attempt} of {attempts}\n"
        f"Return only a valid {spec.output_schema_name} matching the declared "
        "output schema. The previous response failed typed validation. "
        "Do not add commentary, extra fields, or invented evidence."
    )
