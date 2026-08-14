"""The Tier A typed prediction strategy."""

from __future__ import annotations

import asyncio
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
from .prompts import build_method_prompt


class PredictStrategy:
    """Run exactly one isolated Pydantic AI typed invocation."""

    async def execute(
        self,
        parent_agent: Any,
        spec: MethodSpec,
        payload: Any,
        *,
        execution_id: str,
        context_text: str = "",
    ) -> Any:
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
            retries=spec.max_validation_repairs,
        )
        model_name = invocation_agent.get_model_name() or "unknown"
        await on_agent_run_start(parent_agent.name, model_name, execution_id)
        success = False
        error: BaseException | None = None
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
                result = await pydantic_agent.run(
                    prompt,
                    message_history=invocation_agent._message_history,
                    usage_limits=UsageLimits(request_limit=get_message_limit()),
                )
            output = result.output
            if not isinstance(output, spec.output_type):
                raise NativeOutputValidationError(
                    "Pydantic AI returned an unexpected native output type"
                )
            success = True
            return output
        except asyncio.CancelledError as exc:
            error = exc
            raise
        except UnexpectedModelBehavior as exc:
            error = exc
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
