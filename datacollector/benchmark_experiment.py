"""Blind AI-assisted PL:L1 benchmark experiment.

This module deliberately keeps three roles separate:

* an independent-of-submissions reference researcher creates an AI Gold proxy;
* a direct ChatGPT researcher represents the simple one-prompt baseline;
* a second call reviews both methods against the already frozen reference.

The result is an operational AI-assisted benchmark, not a substitute for a
future study with independent human researchers.  Every call retains provider
usage, cost estimates and measured wall-clock duration.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .benchmark import (
    BenchmarkSpec,
    BenchmarkValidationError,
    evaluate_submission,
    load_benchmark_spec,
    load_gold_set,
    load_submission,
    save_gold_set,
    save_submission,
)
from .config import OpenAISettings
from .llm.openai_searcher_client import extract_response_provenance
from .llm.openai_usage import build_agent_usage
from .llm.pricing import build_web_search_tool_usage


class BenchmarkExperimentError(RuntimeError):
    """Raised when an experiment call cannot produce an auditable result."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResearchedField(StrictModel):
    target_field: str
    status: Literal["found", "not_public", "not_applicable"]
    value: str
    source_url: str
    source_type: str
    observed_at: date | None
    valid_as_of: date | None
    notes: str

    @model_validator(mode="after")
    def validate_found(self) -> "ResearchedField":
        if self.status == "found" and not all(
            (
                self.value.strip(),
                self.source_url.strip(),
                self.source_type.strip(),
                self.observed_at,
            )
        ):
            raise ValueError(
                f"{self.target_field}: found requires value, URL, source type and date."
            )
        return self


class ResearchedBrand(StrictModel):
    fields: list[ResearchedField] = Field(min_length=20, max_length=20)
    research_notes: list[str] = Field(max_length=12)


class ReviewedField(StrictModel):
    target_field: str
    direct_decision: Literal[
        "accepted_unchanged", "accepted_edited", "rejected", "gap"
    ]
    direct_corrected_value: str
    pipeline_decision: Literal[
        "accepted_unchanged", "accepted_edited", "rejected", "gap"
    ]
    pipeline_corrected_value: str
    rationale: str


class ReviewedBrand(StrictModel):
    fields: list[ReviewedField] = Field(min_length=20, max_length=20)
    review_notes: list[str] = Field(max_length=12)


def _field_contract(spec: BenchmarkSpec) -> list[dict[str, Any]]:
    return [
        {
            "target_field": field.target_field,
            "label": field.label,
            "value_type": field.value_type,
            "priority": field.priority,
            "freshness_mode": field.freshness_mode,
            "max_age_days": field.max_age_days,
            "accepted_source_types": field.accepted_source_types,
            "minimum_sources": field.minimum_sources,
            "numeric": field.numeric,
        }
        for field in spec.fields
    ]


def _validate_field_scope(
    spec: BenchmarkSpec,
    fields: list[ResearchedField] | list[ReviewedField],
) -> None:
    expected = [field.target_field for field in spec.fields]
    actual = [field.target_field for field in fields]
    if actual != expected:
        raise BenchmarkExperimentError(
            "Provider field order/scope differs from the frozen 20-field contract."
        )


class OpenAIBenchmarkExperiment:
    def __init__(
        self,
        settings: OpenAISettings,
        *,
        client: Any | None = None,
    ):
        self.settings = settings
        self.client = client or OpenAI(
            api_key=settings.api_key,
            timeout=settings.search_timeout_seconds,
            max_retries=0,
        )

    def _research_call(
        self,
        *,
        spec: BenchmarkSpec,
        brand,
        role: Literal["gold", "direct"],
        call_index: int,
        max_search_calls: int,
    ) -> tuple[ResearchedBrand, dict[str, Any]]:
        if role == "gold":
            system = (
                "Jesteś niezależnym badaczem referencyjnym. Nie widzisz i nie wolno "
                "Ci odtwarzać wyników badanego pipeline'u ani metody porównawczej. "
                "Budujesz konserwatywny punkt odniesienia PL:L1 wyłącznie z aktualnie "
                "odnalezionych źródeł. Najpierw używaj oficjalnej polskiej strony, "
                "rejestrów i dokumentów pierwotnych. Nie zgaduj. Jeśli kompetentne "
                "wyszukiwanie nie daje wartości, wybierz not_public."
            )
        else:
            system = (
                "Symulujesz kompetentnego researchera katalogu franczyzowego, który "
                "korzysta bezpośrednio z ChatGPT i wyszukiwarki. Masz przygotować "
                "praktyczny profil PL:L1 w jednym przebiegu, tak jak pracownik "
                "zadający rozbudowane pytanie ChatGPT. Korzystaj z oficjalnej polskiej "
                "strony oraz popularnych wiarygodnych katalogów i źródeł branżowych. "
                "Nie korzystasz z wyników naszego wieloagentowego pipeline'u ani Gold Setu."
            )
        payload = {
            "current_date": datetime.now(timezone.utc).date().isoformat(),
            "country": "PL",
            "brand": {
                "slug": brand.slug,
                "name": brand.name,
                "category": brand.category,
                "source_availability": brand.source_availability,
            },
            "field_contract": _field_contract(spec),
            "rules": [
                "Return exactly one row per field, in the supplied order.",
                "A found value must have a direct URL, source type and observation date.",
                "Use Polish-market facts; clearly reject global values that are not valid for PL.",
                "For numeric facts retain currency, units, VAT qualifiers and as-of date.",
                "Do not infer a missing fee or investment value from a generic offer statement.",
                "Source type must be one concise machine label such as official, registry, industry, marketplace or legal_document.",
            ],
        }
        tool = {
            "type": "web_search",
            "search_context_size": self.settings.search_context_size,
            "external_web_access": True,
            "user_location": {"type": "approximate", "country": "PL"},
        }
        started = time.perf_counter()
        try:
            response = self.client.responses.parse(
                model=self.settings.model,
                reasoning={"effort": self.settings.reasoning_effort},
                max_output_tokens=max(self.settings.max_output_tokens, 8000),
                max_tool_calls=max_search_calls,
                tools=[tool],
                tool_choice="required",
                parallel_tool_calls=False,
                include=["web_search_call.action.sources"],
                store=False,
                metadata={
                    "agent": f"benchmark_{role}",
                    "brand": brand.slug,
                    "call_index": str(call_index),
                },
                input=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                text_format=ResearchedBrand,
            )
        except Exception as exc:
            raise BenchmarkExperimentError(
                f"{role} research failed for {brand.slug} ({type(exc).__name__})."
            ) from exc
        elapsed = time.perf_counter() - started
        draft = getattr(response, "output_parsed", None)
        if draft is None:
            raise BenchmarkExperimentError(
                f"{role} research returned no structured output for {brand.slug}."
            )
        _validate_field_scope(spec, draft.fields)
        actions, _sources, action_counts = extract_response_provenance(
            response,
            call_index=call_index,
            scope_task_ids=[field.target_field for field in spec.fields],
        )
        usage = build_agent_usage(
            response,
            self.settings,
            agent=f"benchmark_{role}",
            iteration=1,
            call_index=call_index,
            scope_task_ids=[field.target_field for field in spec.fields],
            tool_usage=[build_web_search_tool_usage(action_counts)],
        )
        return draft, {
            "role": role,
            "brand": brand.slug,
            "elapsed_seconds": round(elapsed, 4),
            "web_actions": len(actions),
            "usage": usage.model_dump(mode="json"),
        }

    def _review_call(
        self,
        *,
        spec: BenchmarkSpec,
        brand,
        gold_brand,
        direct_brand,
        pipeline_brand,
        call_index: int,
    ) -> tuple[ReviewedBrand, dict[str, Any]]:
        payload = {
            "brand": {"slug": brand.slug, "name": brand.name},
            "field_contract": _field_contract(spec),
            "independent_reference": [
                field.model_dump(mode="json") for field in gold_brand.fields
            ],
            "direct_chatgpt_submission": [
                field.model_dump(mode="json") for field in direct_brand.fields
            ],
            "pipeline_submission": [
                field.model_dump(mode="json") for field in pipeline_brand.fields
            ],
            "decision_rules": [
                "Return exactly one row per field, in supplied order.",
                "Use gap when a method has no proposal.",
                "Use accepted_unchanged only when the proposal is materially supported by the reference.",
                "Use accepted_edited for a useful proposal needing a bounded correction; put the corrected value in corrected_value.",
                "Use rejected for unsupported, wrong-market, stale or contradicted proposals.",
                "Do not reward sterile wording; judge decision usefulness and evidentiary correctness.",
            ],
        }
        started = time.perf_counter()
        try:
            response = self.client.responses.parse(
                model=self.settings.model,
                reasoning={"effort": self.settings.reasoning_effort},
                max_output_tokens=max(self.settings.max_output_tokens, 8000),
                store=False,
                metadata={
                    "agent": "benchmark_reviewer",
                    "brand": brand.slug,
                    "call_index": str(call_index),
                },
                input=[
                    {
                        "role": "system",
                        "content": (
                            "Jesteś oddzielnym audytorem benchmarku. Oceniasz dwie "
                            "metody względem wcześniej zamrożonego, zaślepionego punktu "
                            "odniesienia. Nie dopisuj nowych faktów z pamięci i nie "
                            "faworyzuj bardziej rozbudowanej architektury."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                text_format=ReviewedBrand,
            )
        except Exception as exc:
            raise BenchmarkExperimentError(
                f"Review failed for {brand.slug} ({type(exc).__name__})."
            ) from exc
        elapsed = time.perf_counter() - started
        draft = getattr(response, "output_parsed", None)
        if draft is None:
            raise BenchmarkExperimentError(
                f"Review returned no structured output for {brand.slug}."
            )
        _validate_field_scope(spec, draft.fields)
        usage = build_agent_usage(
            response,
            self.settings,
            agent="benchmark_reviewer",
            iteration=1,
            call_index=call_index,
            scope_task_ids=[field.target_field for field in spec.fields],
        )
        return draft, {
            "role": "review",
            "brand": brand.slug,
            "elapsed_seconds": round(elapsed, 4),
            "web_actions": 0,
            "usage": usage.model_dump(mode="json"),
        }

    def research_gold(self, **kwargs):
        return self._research_call(role="gold", **kwargs)

    def research_direct(self, **kwargs):
        return self._research_call(role="direct", **kwargs)

    def review(self, **kwargs):
        return self._review_call(**kwargs)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _known_report_cost(report: dict[str, Any]) -> Decimal:
    total = Decimal("0")
    for call in report.get("calls", []):
        raw = (call.get("usage", {}).get("cost_estimate") or {}).get(
            "total_estimated_cost_usd"
        )
        if raw is None:
            raise BenchmarkExperimentError("A completed call has unknown cost.")
        total += Decimal(str(raw))
    return total


def _brand_by_slug(artifact, slug: str):
    return next(brand for brand in artifact.brands if brand.slug == slug)


def run_ai_assisted_benchmark(
    *,
    gold_path: Path,
    direct_path: Path,
    pipeline_path: Path,
    report_path: Path,
    settings: OpenAISettings,
    max_cost_usd: Decimal,
    max_search_calls: int = 8,
    client: Any | None = None,
) -> dict[str, Any]:
    """Run/resume the blind 10-brand experiment with per-brand checkpoints."""

    spec = load_benchmark_spec()
    gold = load_gold_set(gold_path)
    direct = load_submission(direct_path)
    pipeline = load_submission(pipeline_path)
    if direct.method != "researcher_chatgpt" or pipeline.method != "pipeline":
        raise BenchmarkValidationError("Experiment received submissions in wrong roles.")
    if any(brand.tasks_attempted != brand.tasks_total for brand in pipeline.brands):
        incomplete = [
            brand.slug
            for brand in pipeline.brands
            if brand.tasks_attempted != brand.tasks_total
        ]
        raise BenchmarkExperimentError(
            "Pipeline tasks are incomplete for: " + ", ".join(incomplete)
        )
    experiment = OpenAIBenchmarkExperiment(settings, client=client)
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        report = {
            "artifact_type": "pl_l1_ai_assisted_experiment",
            "version": "1.0.0",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "model": settings.model,
            "methodology": {
                "gold": "AI-generated independent-of-submissions proxy; not an independent human Gold Set.",
                "direct": "One Responses API web-search prompt per brand for all 20 PL:L1 fields.",
                "review": "Separate model call against frozen Gold; elapsed API wall-clock time, not human labor time.",
                "pipeline": "Existing multi-agent campaign export; only benchmark review is repeated here.",
            },
            "calls": [],
            "brands": {},
        }
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkExperimentError(f"Cannot resume experiment report: {exc}") from exc

    gold.researcher = "OpenAI independent benchmark proxy"
    gold.methodology = "ai_independent_proxy"
    gold.independence_statement = (
        "Generated without access to direct or pipeline submissions. "
        "This is an AI proxy and must not be represented as independent human research."
    )
    gold.provider_model = settings.model
    direct.operator = "OpenAI direct researcher proxy"
    direct.methodology_notes = [
        "One monolithic Responses API request with web search per brand.",
        "No pipeline or Gold Set values were supplied to the direct researcher.",
        "Review times are API wall-clock measurements, not employee active time.",
    ]
    pipeline.methodology_notes = list(
        dict.fromkeys(
            [
                *pipeline.methodology_notes,
                "Benchmark review was repeated by an AI-assisted reviewer against the blind Gold proxy.",
                "Review times are API wall-clock measurements, not employee active time.",
            ]
        )
    )

    for index, definition in enumerate(spec.brands, 1):
        brand_report = report["brands"].setdefault(definition.slug, {})
        gold_brand = _brand_by_slug(gold, definition.slug)
        direct_brand = _brand_by_slug(direct, definition.slug)
        pipeline_brand = _brand_by_slug(pipeline, definition.slug)

        if any(field.status == "pending" for field in gold_brand.fields):
            if _known_report_cost(report) >= max_cost_usd:
                raise BenchmarkExperimentError("Experiment cost ceiling reached before Gold call.")
            draft, call = experiment.research_gold(
                spec=spec,
                brand=definition,
                call_index=index,
                max_search_calls=max_search_calls,
            )
            for target, source in zip(gold_brand.fields, draft.fields, strict=True):
                target.status = source.status
                target.canonical_value = source.value if source.status == "found" else ""
                target.source_url = source.source_url if source.status == "found" else ""
                target.source_type = source.source_type if source.status == "found" else ""
                target.observed_at = source.observed_at if source.status == "found" else None
                target.valid_as_of = source.valid_as_of if source.status == "found" else None
                target.notes = source.notes
            report["calls"].append(call)
            brand_report["gold"] = {
                "completed": True,
                "notes": draft.research_notes,
                "elapsed_seconds": call["elapsed_seconds"],
            }
            save_gold_set(gold_path, gold, overwrite=True)
            _atomic_json(report_path, report)

        if any(field.proposal_status == "not_assessed" for field in direct_brand.fields):
            if _known_report_cost(report) >= max_cost_usd:
                raise BenchmarkExperimentError("Experiment cost ceiling reached before direct call.")
            draft, call = experiment.research_direct(
                spec=spec,
                brand=definition,
                call_index=index,
                max_search_calls=max_search_calls,
            )
            for target, source in zip(direct_brand.fields, draft.fields, strict=True):
                target.proposal_status = (
                    "proposed" if source.status == "found" else "gap"
                )
                target.proposed_value = source.value if source.status == "found" else ""
                target.review_decision = "not_reviewed" if source.status == "found" else "gap"
                target.source_url = source.source_url if source.status == "found" else ""
                target.source_type = source.source_type if source.status == "found" else ""
                target.observed_at = source.observed_at if source.status == "found" else None
                target.valid_as_of = source.valid_as_of if source.status == "found" else None
                target.notes = source.notes
            direct_brand.tasks_attempted = direct_brand.tasks_total
            direct_brand.research_minutes = round(call["elapsed_seconds"] / 60, 2)
            direct_brand.research_measurement = "ai_assisted_wall_clock"
            direct_brand.known_cost_usd = Decimal(
                call["usage"]["cost_estimate"]["total_estimated_cost_usd"]
            )
            report["calls"].append(call)
            brand_report["direct"] = {
                "completed": True,
                "notes": draft.research_notes,
                "elapsed_seconds": call["elapsed_seconds"],
            }
            save_submission(direct_path, direct, overwrite=True)
            _atomic_json(report_path, report)

        if not brand_report.get("review", {}).get("completed"):
            if _known_report_cost(report) >= max_cost_usd:
                raise BenchmarkExperimentError("Experiment cost ceiling reached before review call.")
            # A fully attempted pipeline with no value for a benchmark field is a
            # documented benchmark gap, not an unassessed task.
            for field in pipeline_brand.fields:
                if field.proposal_status == "not_assessed":
                    field.proposal_status = "gap"
                    field.review_decision = "gap"
            draft, call = experiment.review(
                spec=spec,
                brand=definition,
                gold_brand=gold_brand,
                direct_brand=direct_brand,
                pipeline_brand=pipeline_brand,
                call_index=index,
            )
            review_cost = Decimal(
                call["usage"]["cost_estimate"]["total_estimated_cost_usd"]
            )
            for direct_field, pipeline_field, verdict in zip(
                direct_brand.fields,
                pipeline_brand.fields,
                draft.fields,
                strict=True,
            ):
                direct_field.review_decision = (
                    verdict.direct_decision
                    if direct_field.proposal_status == "proposed"
                    else "gap"
                )
                pipeline_field.review_decision = (
                    verdict.pipeline_decision
                    if pipeline_field.proposal_status == "proposed"
                    else "gap"
                )
                if verdict.direct_corrected_value:
                    direct_field.notes = " | ".join(
                        filter(
                            None,
                            [
                                direct_field.notes,
                                f"reviewed_value={verdict.direct_corrected_value}",
                                verdict.rationale,
                            ],
                        )
                    )
                if verdict.pipeline_corrected_value:
                    pipeline_field.notes = " | ".join(
                        filter(
                            None,
                            [
                                pipeline_field.notes,
                                f"reviewed_value={verdict.pipeline_corrected_value}",
                                verdict.rationale,
                            ],
                        )
                    )
            elapsed_minutes = max(round(call["elapsed_seconds"] / 60, 2), 0.01)
            direct_brand.review_minutes = elapsed_minutes
            pipeline_brand.review_minutes = elapsed_minutes
            direct_brand.review_measurement = "ai_assisted_wall_clock"
            pipeline_brand.review_measurement = "ai_assisted_wall_clock"
            # One shared reviewer evaluated both methods. Split its API cost
            # evenly, while retaining the full elapsed latency for each result.
            direct_brand.known_cost_usd += review_cost / 2
            pipeline_brand.known_cost_usd += review_cost / 2
            report["calls"].append(call)
            brand_report["review"] = {
                "completed": True,
                "notes": draft.review_notes,
                "elapsed_seconds": call["elapsed_seconds"],
                "measurement": "ai_assisted_wall_clock",
                "not_human": True,
            }
            save_submission(direct_path, direct, overwrite=True)
            save_submission(pipeline_path, pipeline, overwrite=True)
            _atomic_json(report_path, report)

    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    report["known_experiment_cost_usd"] = str(_known_report_cost(report))
    report["gold_evaluation_status"] = "complete"
    report["direct_evaluation"] = evaluate_submission(spec, direct, gold_set=gold)
    report["pipeline_evaluation"] = evaluate_submission(spec, pipeline, gold_set=gold)
    report["comparison"] = {
        "direct": report["direct_evaluation"]["aggregate"],
        "pipeline": report["pipeline_evaluation"]["aggregate"],
        "interpretation_rule": (
            "Compare cost, elapsed research/review, proposal coverage, acceptance "
            "and exactness together; no single metric determines the winner."
        ),
    }
    _atomic_json(report_path, report)
    return report
