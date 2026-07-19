"""OpenAI Responses API adapter for one bounded Resolver strategy pass."""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from ..config import OpenAISettings
from ..schemas import (
    CheckerResults,
    ResearchPlan,
    ResolverDraft,
    ResolverWorkItem,
    SearchResults,
)
from .openai_usage import build_agent_usage
from .protocol import ResolverGeneration, ResolverProviderError


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


class OpenAIResolverClient:
    """Prioritize locally allowed repair actions without browsing or fetching."""

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
        checker_results: CheckerResults,
        work_items: list[ResolverWorkItem],
        system_prompt: str,
        *,
        iteration: int,
        call_index: int,
    ) -> ResolverGeneration:
        scope_task_ids = list(dict.fromkeys(item.task_id for item in work_items))
        referenced_source_ids = {
            source_id
            for item in work_items
            for source_id in [
                *item.selected_source_ids,
                *item.fallback_source_ids,
            ]
        }
        available_source_ids = [
            source.source_id
            for source in search_results.sources
            if source.source_id in referenced_source_ids
        ]
        search_source_by_id = {
            source.source_id: source for source in search_results.sources
        }
        follow_up_by_id = {
            item.follow_up_id: item for item in checker_results.follow_up_tasks
        }
        payload = {
            "resolver_context": {
                "brand_name": plan.planner_input.brand_name,
                "target_country": plan.planner_input.target_country,
                "depth": plan.planner_input.depth.value,
                "quality_score": checker_results.quality_score,
                "quality_threshold": checker_results.quality_threshold,
                "scope_complete": checker_results.scope_complete,
                "iteration": iteration,
            },
            "known_sources": [
                {
                    "source_id": source_id,
                    "title": search_source_by_id[source_id].title,
                    "canonical_url": search_source_by_id[source_id].canonical_url,
                    "source_type": search_source_by_id[source_id].source_type.value,
                    "task_ids": search_source_by_id[source_id].task_ids,
                    "relevance_note": (
                        search_source_by_id[source_id].relevance_note
                    ),
                }
                for source_id in available_source_ids
            ],
            "work_items": [
                {
                    "follow_up_id": item.follow_up_id,
                    "task_id": item.task_id,
                    "target_field": item.target_field,
                    "priority": item.priority.value,
                    "reason": item.reason.value,
                    "allowed_actions": [
                        action.value for action in item.allowed_actions
                    ],
                    "candidate_source_ids": follow_up_by_id[
                        item.follow_up_id
                    ].candidate_source_ids,
                    "retry_source_ids": follow_up_by_id[
                        item.follow_up_id
                    ].retry_source_ids,
                    "reextract_source_ids": follow_up_by_id[
                        item.follow_up_id
                    ].reextract_source_ids,
                    "existing_queries": item.queries,
                    "minimum_additional_sources": (
                        item.minimum_additional_sources
                    ),
                    "requires_independent_source": (
                        item.requires_independent_source
                    ),
                    "completion_criteria": item.completion_criteria,
                    "deterministic_action": item.selected_action.value,
                }
                for item in work_items
            ],
            "required_output": {
                "follow_up_ids": [item.follow_up_id for item in work_items],
                "rule": (
                    "Return exactly one item for every supplied follow_up_id. "
                    "Use only an allowed action and source IDs from that action's "
                    "corresponding source list. Sequence values must be a complete "
                    "one-based ordering."
                ),
            },
            "instruction": (
                "Prioritize only the supplied repair work. Prefer already known, "
                "unevaluated sources; use retry for a likely useful failed document; "
                "use re-extraction only when existing parsed content may contain the "
                "field; search only when known evidence cannot close the gap. Do not "
                "browse, fetch, extract facts, or claim that any action was executed."
            ),
        }
        failure_context = {
            "agent": "resolver",
            "iteration": iteration,
            "call_index": call_index,
            "scope_task_ids": scope_task_ids,
            "scope_source_ids": available_source_ids,
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
                    "agent": "resolver",
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
                text_format=ResolverDraft,
                **cache_options,
            )
        except Exception as exc:
            raise ResolverProviderError(
                f"OpenAI Resolver request failed ({type(exc).__name__}).",
                code="provider_exception",
                **failure_context,
            ) from None

        try:
            usage = build_agent_usage(
                response,
                self.settings,
                agent="resolver",
                iteration=iteration,
                call_index=call_index,
                scope_task_ids=scope_task_ids,
                scope_source_ids=available_source_ids,
            )
        except ValueError:
            raise ResolverProviderError(
                "OpenAI Resolver response did not contain valid token usage.",
                code="invalid_usage",
                **failure_context,
            ) from None

        response_status = getattr(response, "status", None)
        if response_status not in (None, "completed"):
            raise ResolverProviderError(
                f"OpenAI Resolver response ended with status {response_status!r}.",
                code="incomplete_response",
                usage=usage,
                **failure_context,
            )
        if _response_contains_refusal(response):
            raise ResolverProviderError(
                "OpenAI Resolver refused the structured planning request.",
                code="refusal",
                usage=usage,
                **failure_context,
            )
        draft = getattr(response, "output_parsed", None)
        if draft is None:
            raise ResolverProviderError(
                "OpenAI Resolver response did not contain structured output.",
                code="missing_structured_output",
                usage=usage,
                **failure_context,
            )
        if not isinstance(draft, ResolverDraft):
            try:
                draft = ResolverDraft.model_validate(draft)
            except Exception:
                raise ResolverProviderError(
                    "OpenAI Resolver structured output failed schema validation.",
                    code="invalid_structured_output",
                    usage=usage,
                    **failure_context,
                ) from None
        return ResolverGeneration(draft=draft, usage=usage)
