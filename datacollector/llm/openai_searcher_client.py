"""OpenAI Responses web-search adapter for the Searcher agent."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI

from ..config import OpenAISettings
from ..schemas import (
    ResearchPlan,
    ResearchTask,
    SearchAction,
    SearcherDraft,
)
from .openai_usage import build_agent_usage
from .pricing import build_web_search_tool_usage
from .protocol import ProviderSearchSource, SearcherGeneration, SearcherProviderError


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return vars(value)
    return {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _source_from_mapping(value: Any) -> ProviderSearchSource | None:
    payload = _as_mapping(value)
    url = payload.get("url")
    if not isinstance(url, str) or not url.strip():
        return None
    title = payload.get("title")
    return ProviderSearchSource(
        url=url.strip(),
        title=title.strip() if isinstance(title, str) else "",
    )


def _extract_response_provenance(
    response: Any,
    *,
    call_index: int,
    scope_task_ids: list[str],
) -> tuple[list[SearchAction], list[ProviderSearchSource], dict[str, int]]:
    actions: list[SearchAction] = []
    sources_by_url: dict[str, ProviderSearchSource] = {}
    action_counts: Counter[str] = Counter()

    for output_index, raw_item in enumerate(getattr(response, "output", []) or [], 1):
        item = _as_mapping(raw_item)
        item_type = item.get("type")
        if item_type == "web_search_call":
            raw_action = _as_mapping(item.get("action"))
            action_type = str(raw_action.get("type") or "unknown")
            action_status = str(item.get("status") or "completed")
            action_counts[action_type] += 1
            queries = _string_list(raw_action.get("queries"))
            if not queries:
                queries = _string_list(raw_action.get("query"))

            action_sources: list[str] = []
            if action_status == "completed":
                for raw_source in raw_action.get("sources") or []:
                    source = _source_from_mapping(raw_source)
                    if source is None:
                        continue
                    existing = sources_by_url.get(source.url)
                    if existing is None or (not existing.title and source.title):
                        sources_by_url[source.url] = source
                    action_sources.append(source.url)

            target_url = raw_action.get("url")
            if (
                action_status == "completed"
                and action_type in {"open_page", "find_in_page"}
                and isinstance(target_url, str)
            ):
                sources_by_url.setdefault(
                    target_url,
                    ProviderSearchSource(url=target_url),
                )
            actions.append(
                SearchAction(
                    action_id=(
                        str(item["id"])
                        if item.get("id") is not None
                        else f"call-{call_index:03d}-action-{output_index:03d}"
                    ),
                    call_index=call_index,
                    scope_task_ids=scope_task_ids,
                    action_type=action_type,
                    status=action_status,
                    queries=list(dict.fromkeys(queries)),
                    target_url=(
                        target_url if isinstance(target_url, str) else None
                    ),
                    source_urls=list(dict.fromkeys(action_sources)),
                )
            )
            continue

        if item_type != "message":
            continue
        for raw_content in item.get("content") or []:
            content = _as_mapping(raw_content)
            for raw_annotation in content.get("annotations") or []:
                annotation = _as_mapping(raw_annotation)
                if annotation.get("type") != "url_citation":
                    continue
                source = _source_from_mapping(annotation)
                if source is not None:
                    existing = sources_by_url.get(source.url)
                    if existing is None or (not existing.title and source.title):
                        sources_by_url[source.url] = source

    return actions, list(sources_by_url.values()), dict(action_counts)


def _task_payload(task: ResearchTask) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "catalog_question_id": task.catalog_question_id,
        "title": task.title,
        "question": task.question,
        "priority": task.priority.value,
        "target_fields": task.target_fields,
        "preferred_source_types": [item.value for item in task.preferred_source_types],
        "source_hints": task.source_hints,
        "search_queries": task.search_queries,
        "acceptance_criteria": task.acceptance_criteria,
        "minimum_sources": task.min_sources,
        "requires_independent_corroboration": task.requires_independent_corroboration,
        "max_age_days": task.max_age_days,
        "sensitivity": task.sensitivity.value,
        "depends_on": task.depends_on,
    }


class OpenAISearcherClient:
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
        plan: ResearchPlan,
        tasks: list[ResearchTask],
        system_prompt: str,
        *,
        iteration: int,
        call_index: int,
        max_search_calls: int,
        min_queries_per_task: int,
    ) -> SearcherGeneration:
        payload = {
            "search_context": {
                "current_date": datetime.now(timezone.utc).date().isoformat(),
                "brand_name": plan.planner_input.brand_name,
                "target_country": plan.planner_input.target_country,
                "target_regions": plan.planner_input.target_regions,
                "research_languages": plan.planner_input.research_languages,
                "known_legal_name": plan.planner_input.known_legal_name,
                "known_official_website": (
                    plan.planner_input.known_official_website
                ),
            },
            "tasks": [
                {
                    **_task_payload(task),
                    "minimum_query_attempts": min(
                        min_queries_per_task,
                        len(task.search_queries),
                    ),
                }
                for task in tasks
            ],
            "source_policy": plan.source_policy.model_dump(mode="json"),
            "compliance_rules": plan.compliance_rules,
            "instruction": (
                "Discover public source candidates only. Do not extract a final "
                "profile or assert normalized facts. For every task, issue at "
                "least minimum_query_attempts provided search_queries exactly as "
                "written before optional derived queries. Map every issued query "
                "and every retained URL to its task."
            ),
        }
        web_search_tool: dict[str, Any] = {
            "type": "web_search",
            "search_context_size": "medium",
            "external_web_access": True,
            "user_location": {
                "type": "approximate",
                "country": plan.planner_input.target_country,
            },
        }
        if plan.planner_input.target_regions:
            web_search_tool["user_location"]["region"] = ", ".join(
                plan.planner_input.target_regions
            )

        request_options = (
            {"prompt_cache_options": {"mode": "explicit"}}
            if self.settings.model.startswith("gpt-5.6")
            else {}
        )
        try:
            response = self._client.responses.parse(
                model=self.settings.model,
                reasoning={"effort": self.settings.reasoning_effort},
                max_output_tokens=self.settings.max_output_tokens,
                max_tool_calls=max_search_calls,
                tools=[web_search_tool],
                tool_choice="required",
                include=["web_search_call.action.sources"],
                store=False,
                metadata={
                    "agent": "searcher",
                    "iteration": str(iteration),
                    "call_index": str(call_index),
                    "plan_run_id": plan.run_id,
                },
                input=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                text_format=SearcherDraft,
                **request_options,
            )
        except Exception as exc:
            raise SearcherProviderError(
                f"OpenAI Searcher request failed ({type(exc).__name__}).",
                code="provider_exception",
            ) from None

        actions, provider_sources, action_counts = _extract_response_provenance(
            response,
            call_index=call_index,
            scope_task_ids=[task.task_id for task in tasks],
        )
        tool_usage = [build_web_search_tool_usage(action_counts)]
        failure_context = {
            "observed_tool_calls": len(actions),
            "tool_usage": tool_usage,
            "agent": "searcher",
            "iteration": iteration,
            "call_index": call_index,
            "scope_task_ids": [task.task_id for task in tasks],
            "requested_model": self.settings.model,
        }
        try:
            usage = build_agent_usage(
                response,
                self.settings,
                agent="searcher",
                iteration=iteration,
                call_index=call_index,
                scope_task_ids=[task.task_id for task in tasks],
                tool_usage=tool_usage,
            )
        except ValueError:
            raise SearcherProviderError(
                "OpenAI Searcher response did not contain valid token usage.",
                code="invalid_usage",
                **failure_context,
            ) from None

        response_status = getattr(response, "status", None)
        if response_status not in (None, "completed"):
            raise SearcherProviderError(
                f"OpenAI Searcher response ended with status {response_status!r}.",
                code="incomplete_response",
                usage=usage,
                **failure_context,
            )
        draft = getattr(response, "output_parsed", None)
        if draft is None:
            raise SearcherProviderError(
                "OpenAI Searcher response did not contain parsed structured output.",
                code="missing_structured_output",
                usage=usage,
                **failure_context,
            )
        if not any(
            action.action_type == "search" and action.status == "completed"
            for action in actions
        ):
            raise SearcherProviderError(
                "OpenAI Searcher response did not contain a completed web "
                "search action.",
                code="missing_completed_search",
                usage=usage,
                **failure_context,
            )

        return SearcherGeneration(
            draft=draft,
            usage=usage,
            actions=actions,
            provider_sources=provider_sources,
        )
