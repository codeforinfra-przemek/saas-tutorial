"""OpenAI Responses API adapter for Planner structured output."""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from ..config import OpenAISettings
from ..schemas import CatalogQuestion, PlannerDraft, PlannerInput


class PlannerProviderError(RuntimeError):
    """Raised when the OpenAI provider does not produce a usable draft."""


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
    ) -> PlannerDraft:
        payload = {
            "planner_input": planner_input.model_dump(mode="json"),
            "canonical_questions": [
                question.model_dump(mode="json") for question in questions
            ],
            "instruction": (
                "Return planning guidance only. Canonical questions will be merged "
                "deterministically after your response."
            ),
        }
        try:
            response = self._client.responses.parse(
                model=self.settings.model,
                reasoning={"effort": self.settings.reasoning_effort},
                max_output_tokens=self.settings.max_output_tokens,
                store=False,
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
        return draft
