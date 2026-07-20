"""OpenAI Responses API adapter for one bounded Normalizer pass."""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from ..config import OpenAISettings
from ..schemas import (
    CheckerResults,
    ExtractionResults,
    NormalizerDraft,
    ResearchPlan,
    SearchResults,
)
from .openai_usage import build_agent_usage
from .protocol import NormalizerGeneration, NormalizerProviderError


def _response_contains_refusal(response: Any) -> bool:
    for item in getattr(response, "output", []) or []:
        contents = item.get("content", []) if isinstance(item, dict) else getattr(
            item, "content", []
        )
        for content in contents or []:
            content_type = (
                content.get("type")
                if isinstance(content, dict)
                else getattr(content, "type", None)
            )
            refusal = (
                content.get("refusal")
                if isinstance(content, dict)
                else getattr(content, "refusal", None)
            )
            if content_type == "refusal" or refusal:
                return True
    return False


class OpenAINormalizerClient:
    """Normalize accepted Checker claims without tools or outside knowledge."""

    def __init__(self, settings: OpenAISettings, client: Any | None = None):
        self.settings = settings
        self._client = client or OpenAI(
            api_key=settings.api_key,
            timeout=settings.timeout_seconds,
            max_retries=0,
        )

    @property
    def model_name(self) -> str:
        return self.settings.model

    def generate(
        self,
        plan: ResearchPlan,
        search_results: SearchResults,
        extraction_results: ExtractionResults,
        checker_results: CheckerResults,
        claim_ids: list[str],
        system_prompt: str,
        *,
        iteration: int,
        call_index: int,
    ) -> NormalizerGeneration:
        claim_id_set = set(claim_ids)
        claim_by_id = {
            claim.claim_id: claim
            for claim in extraction_results.claims
            if claim.claim_id in claim_id_set
        }
        decision_by_id = {
            decision.claim_id: decision
            for decision in checker_results.claim_decisions
            if decision.claim_id in claim_id_set
        }
        citation_by_id = {
            citation.citation_id: citation
            for citation in extraction_results.citations
        }
        source_by_id = {source.source_id: source for source in search_results.sources}
        scope_task_ids = list(
            dict.fromkeys(claim_by_id[claim_id].task_id for claim_id in claim_ids)
        )
        scope_source_ids = list(
            dict.fromkeys(
                citation_by_id[citation_id].source_id
                for claim_id in claim_ids
                for citation_id in claim_by_id[claim_id].citation_ids
            )
        )
        payload = {
            "normalizer_context": {
                "brand_name": plan.planner_input.brand_name,
                "target_country": plan.planner_input.target_country,
                "research_languages": plan.planner_input.research_languages,
                "quality_score": checker_results.quality_score,
                "quality_threshold": checker_results.quality_threshold,
                "checker_passed": checker_results.passed,
                "scope_complete": checker_results.scope_complete,
                "iteration": iteration,
            },
            "accepted_claims": [
                {
                    "claim_id": claim_id,
                    "task_id": claim_by_id[claim_id].task_id,
                    "target_field": claim_by_id[claim_id].target_field,
                    "value_text": claim_by_id[claim_id].value_text,
                    "asserted_by_text": claim_by_id[claim_id].asserted_by_text,
                    "as_of_text": claim_by_id[claim_id].as_of_text,
                    "unit_text": claim_by_id[claim_id].unit_text,
                    "currency_text": claim_by_id[claim_id].currency_text,
                    "publication_date_text": (
                        claim_by_id[claim_id].publication_date_text
                    ),
                    "effective_date_text": (
                        claim_by_id[claim_id].effective_date_text
                    ),
                    "checker_semantic_fit": (
                        decision_by_id[claim_id].semantic_fit.value
                    ),
                    "checker_source_support": (
                        decision_by_id[claim_id].source_support.value
                    ),
                    "citations": [
                        {
                            "citation_id": citation_id,
                            "source_id": citation_by_id[citation_id].source_id,
                            "quote": citation_by_id[citation_id].quote,
                            "locator": citation_by_id[citation_id].locator,
                        }
                        for citation_id in claim_by_id[claim_id].citation_ids
                    ],
                }
                for claim_id in claim_ids
            ],
            "sources": [
                {
                    "source_id": source_id,
                    "canonical_url": source_by_id[source_id].canonical_url,
                    "title": source_by_id[source_id].title,
                    "source_type": source_by_id[source_id].source_type.value,
                }
                for source_id in scope_source_ids
            ],
            "required_output": {
                "claim_ids": claim_ids,
                "rule": (
                    "Cover every supplied claim_id exactly once. Group claims only "
                    "when their task, field, meaning, scope, period, unit, and "
                    "currency are equivalent."
                ),
            },
        }
        failure_context = {
            "agent": "normalizer",
            "iteration": iteration,
            "call_index": call_index,
            "scope_task_ids": scope_task_ids,
            "scope_source_ids": scope_source_ids,
            "requested_model": self.settings.model,
        }
        try:
            cache_options = (
                {"prompt_cache_options": {"mode": "explicit"}}
                if self.settings.model.startswith("gpt-5.6")
                else {}
            )
            response = self._client.responses.parse(
                model=self.settings.model,
                reasoning={"effort": self.settings.reasoning_effort},
                max_output_tokens=self.settings.max_output_tokens,
                store=False,
                metadata={
                    "agent": "normalizer",
                    "iteration": str(iteration),
                    "call_index": str(call_index),
                    "plan_run_id": plan.run_id,
                    "check_id": checker_results.check_id,
                },
                input=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                text_format=NormalizerDraft,
                **cache_options,
            )
        except Exception as exc:
            raise NormalizerProviderError(
                f"OpenAI Normalizer request failed ({type(exc).__name__}).",
                code="provider_exception",
                **failure_context,
            ) from None

        try:
            usage = build_agent_usage(
                response,
                self.settings,
                agent="normalizer",
                iteration=iteration,
                call_index=call_index,
                scope_task_ids=scope_task_ids,
                scope_source_ids=scope_source_ids,
            )
        except ValueError:
            raise NormalizerProviderError(
                "OpenAI Normalizer response did not contain valid token usage.",
                code="invalid_usage",
                **failure_context,
            ) from None

        response_status = getattr(response, "status", None)
        if response_status not in (None, "completed"):
            raise NormalizerProviderError(
                f"OpenAI Normalizer response ended with status {response_status!r}.",
                code="incomplete_response",
                usage=usage,
                **failure_context,
            )
        if _response_contains_refusal(response):
            raise NormalizerProviderError(
                "OpenAI Normalizer refused the structured request.",
                code="refusal",
                usage=usage,
                **failure_context,
            )
        draft = getattr(response, "output_parsed", None)
        if draft is None:
            raise NormalizerProviderError(
                "OpenAI Normalizer response did not contain structured output.",
                code="missing_structured_output",
                usage=usage,
                **failure_context,
            )
        if not isinstance(draft, NormalizerDraft):
            try:
                draft = NormalizerDraft.model_validate(draft)
            except Exception:
                raise NormalizerProviderError(
                    "OpenAI Normalizer structured output failed schema validation.",
                    code="invalid_structured_output",
                    usage=usage,
                    **failure_context,
                ) from None
        return NormalizerGeneration(draft=draft, usage=usage)
