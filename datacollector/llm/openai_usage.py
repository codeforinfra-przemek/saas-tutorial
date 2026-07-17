"""Convert one completed OpenAI response into auditable usage metadata."""

from __future__ import annotations

from typing import Any

from ..config import OpenAISettings
from ..schemas import AgentIterationUsage, TokenUsage, ToolUsage
from .pricing import estimate_standard_token_cost


def build_agent_usage(
    response: Any,
    settings: OpenAISettings,
    *,
    agent: str,
    iteration: int,
    call_index: int = 1,
    scope_task_ids: list[str] | None = None,
    tool_usage: list[ToolUsage] | None = None,
) -> AgentIterationUsage:
    provider_usage = getattr(response, "usage", None)
    if provider_usage is None:
        raise ValueError("OpenAI response did not contain token usage.")

    input_details = getattr(provider_usage, "input_tokens_details", None)
    output_details = getattr(provider_usage, "output_tokens_details", None)
    token_usage = TokenUsage(
        input_tokens=provider_usage.input_tokens,
        cached_input_tokens=(
            getattr(input_details, "cached_tokens", 0)
            if input_details is not None
            else 0
        ),
        cache_write_input_tokens=(
            getattr(input_details, "cache_write_tokens", 0)
            if input_details is not None
            else 0
        ),
        output_tokens=provider_usage.output_tokens,
        reasoning_tokens=(
            getattr(output_details, "reasoning_tokens", 0)
            if output_details is not None
            else 0
        ),
        total_tokens=provider_usage.total_tokens,
    )
    resolved_model = getattr(response, "model", None) or settings.model
    service_tier = getattr(response, "service_tier", None)
    recorded_tool_usage = tool_usage or []
    return AgentIterationUsage(
        agent=agent,
        iteration=iteration,
        call_index=call_index,
        scope_task_ids=scope_task_ids or [],
        requested_model=settings.model,
        resolved_model=resolved_model,
        response_id=getattr(response, "id", None),
        request_id=getattr(response, "_request_id", None),
        service_tier=service_tier,
        tokens=token_usage,
        tool_usage=recorded_tool_usage,
        cost_estimate=estimate_standard_token_cost(
            resolved_model,
            token_usage,
            service_tier=service_tier,
            tool_usage=recorded_tool_usage,
        ),
    )
