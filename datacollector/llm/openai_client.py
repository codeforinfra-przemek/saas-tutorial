"""OpenAI Responses API adapter for Planner structured output."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI

from ..config import OpenAISettings
from ..schemas import (
    AgentIterationUsage,
    CatalogQuestion,
    PlannerDraft,
    PlannerInput,
    TokenUsage,
)
from .pricing import estimate_standard_token_cost
from .protocol import PlannerGeneration


class PlannerProviderError(RuntimeError):
    """Raised when the OpenAI provider does not produce a usable draft."""


def _compact_question_payload(question: CatalogQuestion) -> dict[str, Any]:
    """Keep planning context while excluding deterministic execution metadata."""

    return {
        "id": question.id,
        "title": question.title,
        "question": question.question,
        "fdd_items": question.fdd_items,
        "requirement": question.requirement.value,
        "target_fields": question.target_fields,
        "preferred_source_types": [
            source_type.value
            for source_type in question.evidence.preferred_source_types
        ],
        "requires_independent_corroboration": (
            question.evidence.requires_independent_corroboration
        ),
        "search_query_templates": question.search_query_templates,
        "dependencies": question.dependencies,
        "sensitivity": question.sensitivity.value,
    }


class OpenAIPlannerClient:
    def __init__(self, settings: OpenAISettings, client: Any | None = None):
        self.settings = settings
        self._client = client or OpenAI(
            api_key=settings.api_key,
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
        )

    @property
    def model_name(self) -> str:
        return self.settings.model

    def generate(
        self,
        planner_input: PlannerInput,
        questions: list[CatalogQuestion],
        system_prompt: str,
        *,
        iteration: int,
    ) -> PlannerGeneration:
        payload = {
            "planning_context": {
                "current_date": datetime.now(timezone.utc).date().isoformat(),
            },
            "planner_input": planner_input.model_dump(mode="json"),
            "canonical_questions": [
                _compact_question_payload(question) for question in questions
            ],
            "instruction": (
                "Return planning guidance only. Canonical questions will be merged "
                "deterministically after your response."
            ),
        }
        try:
            cache_options = (
                {"mode": "explicit"}
                if self.settings.model.startswith("gpt-5.6")
                else None
            )
            response = self._client.responses.parse(
                model=self.settings.model,
                reasoning={"effort": self.settings.reasoning_effort},
                max_output_tokens=self.settings.max_output_tokens,
                store=False,
                **(
                    {"prompt_cache_options": cache_options}
                    if cache_options is not None
                    else {}
                ),
                metadata={"agent": "planner", "iteration": str(iteration)},
                input=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                text_format=PlannerDraft,
            )
        except Exception as exc:  # The SDK exposes several transport/API errors.
            raise PlannerProviderError(
                f"OpenAI Planner request failed ({type(exc).__name__})."
            ) from None

        draft = response.output_parsed
        if draft is None:
            raise PlannerProviderError(
                "OpenAI Planner response did not contain parsed structured output."
            )

        provider_usage = response.usage
        if provider_usage is None:
            raise PlannerProviderError(
                "OpenAI Planner response did not contain token usage."
            )
        input_details = provider_usage.input_tokens_details
        output_details = provider_usage.output_tokens_details
        token_usage = TokenUsage(
            input_tokens=provider_usage.input_tokens,
            cached_input_tokens=(
                input_details.cached_tokens if input_details is not None else 0
            ),
            cache_write_input_tokens=(
                getattr(input_details, "cache_write_tokens", 0)
                if input_details is not None
                else 0
            ),
            output_tokens=provider_usage.output_tokens,
            reasoning_tokens=(
                output_details.reasoning_tokens if output_details is not None else 0
            ),
            total_tokens=provider_usage.total_tokens,
        )
        resolved_model = response.model or self.settings.model
        service_tier = getattr(response, "service_tier", None)
        usage = AgentIterationUsage(
            agent="planner",
            iteration=iteration,
            requested_model=self.settings.model,
            resolved_model=resolved_model,
            response_id=getattr(response, "id", None),
            request_id=getattr(response, "_request_id", None),
            service_tier=service_tier,
            tokens=token_usage,
            cost_estimate=estimate_standard_token_cost(
                resolved_model,
                token_usage,
                service_tier=service_tier,
            ),
        )
        return PlannerGeneration(draft=draft, usage=usage)
