"""Deterministic Human Review gate and readable evidence report."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
from uuid import uuid4

from ..schemas import (
    CheckerResults,
    ExtractionResults,
    HumanReviewCoverage,
    HumanReviewDecision,
    HumanReviewResults,
    NormalizerMode,
    NormalizerResults,
    NormalizerStrategySource,
    ResearchPlan,
    SearchResults,
)


class HumanReviewValidationError(ValueError):
    """Raised when review lineage or a human decision is unsafe."""


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


class HumanReviewer:
    """Create an immutable decision and a human-readable, non-AI report."""

    def create_review(
        self,
        plan: ResearchPlan,
        search_results: SearchResults,
        extraction_results: ExtractionResults,
        checker_results: CheckerResults,
        normalizer_results: NormalizerResults,
        *,
        plan_sha256: str,
        search_sha256: str,
        extraction_sha256: str,
        check_sha256: str,
        normalized_sha256: str,
        normalized_reference: str,
        report_reference: str,
        decision: HumanReviewDecision = HumanReviewDecision.PENDING,
        reviewer: str | None = None,
        reviewer_notes: str = "",
        acknowledge_incomplete: bool = False,
    ) -> HumanReviewResults:
        self._validate_lineage(
            plan,
            search_results,
            extraction_results,
            checker_results,
            normalizer_results,
            plan_sha256=plan_sha256,
            search_sha256=search_sha256,
            extraction_sha256=extraction_sha256,
            check_sha256=check_sha256,
            normalized_reference=normalized_reference,
        )
        if decision == HumanReviewDecision.PENDING and reviewer:
            raise HumanReviewValidationError(
                "Pending review cannot be signed; choose a final --decision."
            )
        if decision != HumanReviewDecision.PENDING and not (reviewer or "").strip():
            raise HumanReviewValidationError(
                "A final Human Review decision requires --reviewer."
            )
        if decision in {
            HumanReviewDecision.APPROVED,
            HumanReviewDecision.APPROVED_WITH_GAPS,
        } and (
            normalizer_results.normalization_mode != NormalizerMode.PAID
            or normalizer_results.strategy_source
            not in {
                NormalizerStrategySource.OPENAI,
                NormalizerStrategySource.OPENAI_REPAIRED,
            }
        ):
            raise HumanReviewValidationError(
                "Import approval requires a successful paid Normalizer result."
            )
        if decision == HumanReviewDecision.APPROVED and (
            not normalizer_results.input_checker_passed
            or not normalizer_results.input_scope_complete
        ):
            raise HumanReviewValidationError(
                "Incomplete research cannot use approved; use approved_with_gaps "
                "with --acknowledge-incomplete."
            )
        if (
            decision == HumanReviewDecision.APPROVED_WITH_GAPS
            and not acknowledge_incomplete
        ):
            raise HumanReviewValidationError(
                "Approval with gaps requires --acknowledge-incomplete."
            )

        planned_field_keys = {
            (task.task_id, target_field)
            for task in plan.tasks
            for target_field in task.target_fields
        }
        evaluated_field_keys = {
            (field.task_id, field.target_field)
            for field in normalizer_results.field_results
        }
        evaluated_task_ids = {task_id for task_id, _ in evaluated_field_keys}
        populated_field_keys = {
            (value.task_id, value.target_field)
            for value in normalizer_results.normalized_values
        }
        coverage = HumanReviewCoverage(
            planned_tasks=len(plan.tasks),
            evaluated_tasks=len(evaluated_task_ids),
            planned_fields=len(planned_field_keys),
            evaluated_fields=len(evaluated_field_keys),
            fields_with_values=len(populated_field_keys),
            unresolved_fields=len(normalizer_results.unresolved_field_ids),
            critical_missing_fields=len(normalizer_results.critical_missing_fields),
            unevaluated_critical_fields=len(
                normalizer_results.unevaluated_critical_fields
            ),
            normalized_values=len(normalizer_results.normalized_values),
            accepted_claims=len(normalizer_results.eligible_claim_ids),
            excluded_claims=len(normalizer_results.excluded_claim_ids),
            sources=len(search_results.sources),
            citations=len(extraction_results.citations),
        )
        warnings = _deduplicate(
            [
                *normalizer_results.warnings,
                "Human Review approval authorizes database import only; public "
                "presentation must retain evidence status and documented gaps.",
            ]
        )
        return HumanReviewResults(
            review_id=str(uuid4()),
            normalization_id=normalizer_results.normalization_id,
            normalized_sha256=normalized_sha256,
            normalized_reference=str(Path(normalized_reference).resolve()),
            report_reference=str(Path(report_reference).resolve()),
            created_at=datetime.now(timezone.utc),
            iteration=normalizer_results.iteration,
            brand_name=normalizer_results.brand_name,
            target_country=normalizer_results.target_country,
            depth=normalizer_results.depth,
            decision=decision,
            reviewer=reviewer.strip() if reviewer else None,
            reviewer_notes=reviewer_notes.strip(),
            incomplete_input_acknowledged=acknowledge_incomplete,
            input_checker_passed=normalizer_results.input_checker_passed,
            input_scope_complete=normalizer_results.input_scope_complete,
            input_quality_score=normalizer_results.input_quality_score,
            input_quality_threshold=normalizer_results.input_quality_threshold,
            approved_for_import=decision
            in {
                HumanReviewDecision.APPROVED,
                HumanReviewDecision.APPROVED_WITH_GAPS,
            },
            coverage=coverage,
            warnings=warnings,
        )

    @staticmethod
    def _validate_lineage(
        plan: ResearchPlan,
        search_results: SearchResults,
        extraction_results: ExtractionResults,
        checker_results: CheckerResults,
        normalizer_results: NormalizerResults,
        *,
        plan_sha256: str,
        search_sha256: str,
        extraction_sha256: str,
        check_sha256: str,
        normalized_reference: str,
    ) -> None:
        if (
            normalizer_results.plan_run_id != plan.run_id
            or normalizer_results.search_id != search_results.search_id
            or normalizer_results.extraction_id != extraction_results.extraction_id
            or normalizer_results.check_id != checker_results.check_id
        ):
            raise HumanReviewValidationError(
                "Human Review input IDs do not match Normalizer lineage."
            )
        for actual, expected, label in (
            (plan_sha256, normalizer_results.plan_sha256, "Plan"),
            (search_sha256, normalizer_results.search_sha256, "Searcher"),
            (extraction_sha256, normalizer_results.extraction_sha256, "Extractor"),
            (check_sha256, normalizer_results.check_sha256, "Checker"),
        ):
            if actual != expected:
                raise HumanReviewValidationError(
                    f"Human Review {label} bytes do not match Normalizer lineage."
                )
        if not Path(normalized_reference).is_file():
            raise HumanReviewValidationError(
                "Human Review requires an existing Normalizer artifact."
            )


def _value_display(value) -> str:
    if value.value_type.value in {"integer", "decimal", "money", "percentage"}:
        rendered = str(value.number_min)
        if value.number_max is not None and value.number_max != value.number_min:
            rendered = f"{rendered} – {value.number_max}"
        suffix = value.currency or value.unit or ("%" if value.value_type.value == "percentage" else "")
        return f"{rendered} {suffix}".strip()
    if value.value_type.value == "boolean":
        return "Tak" if value.boolean_value else "Nie"
    if value.value_type.value == "date":
        return value.date_value.isoformat() if value.date_value else value.canonical_text
    return value.canonical_text


def render_review_html(
    review: HumanReviewResults,
    plan: ResearchPlan,
    search_results: SearchResults,
    extraction_results: ExtractionResults,
    checker_results: CheckerResults,
    normalizer_results: NormalizerResults,
) -> str:
    """Render one portable report with coverage, gaps, evidence and sources."""

    field_by_key = {
        (field.task_id, field.target_field): field
        for field in normalizer_results.field_results
    }
    values_by_key: dict[tuple[str, str], list] = {}
    for value in normalizer_results.normalized_values:
        values_by_key.setdefault((value.task_id, value.target_field), []).append(value)
    claim_by_id = {claim.claim_id: claim for claim in extraction_results.claims}
    citation_by_id = {
        citation.citation_id: citation for citation in extraction_results.citations
    }
    source_by_id = {source.source_id: source for source in search_results.sources}
    retrieval_by_source = {
        document.source_id: document.retrieval_status.value
        for document in extraction_results.documents
    }

    def badge(text: str, tone: str = "muted") -> str:
        return f'<span class="badge {tone}">{escape(text)}</span>'

    def value_card(value) -> str:
        citations = []
        for citation_id in value.citation_ids:
            citation = citation_by_id.get(citation_id)
            if citation is None:
                continue
            source = source_by_id.get(citation.source_id)
            source_link = escape(citation.source_id)
            if source is not None:
                source_link = (
                    f'<a href="{escape(str(source.canonical_url), quote=True)}" '
                    f'target="_blank" rel="noopener">{escape(source.title or source.canonical_url)}</a>'
                )
            citations.append(
                '<details class="evidence"><summary>Dowód: '
                f'{source_link} · {escape(citation.locator or "bez lokalizatora")}'
                f'</summary><blockquote>{escape(citation.quote)}</blockquote></details>'
            )
        raw = "".join(
            f"<li>{escape(text)}</li>" for text in value.raw_value_texts
        )
        return (
            '<article class="value">'
            f'<div class="value-head"><strong>{escape(_value_display(value))}</strong>'
            f'{badge(value.value_type.value, "info")}'
            f'{badge(value.precision.value)}'
            f'{badge("wymaga potwierdzenia", "warn") if value.needs_corroboration else ""}'
            '</div>'
            f'<p class="canonical">{escape(value.canonical_text)}</p>'
            f'<details><summary>Surowe wartości i identyfikatory</summary><ul>{raw}</ul>'
            f'<code>{escape(", ".join(value.claim_ids))}</code></details>'
            f'{"".join(citations)}'
            '</article>'
        )

    task_sections = []
    for task in plan.tasks:
        fields = []
        for target_field in task.target_fields:
            key = (task.task_id, target_field)
            field = field_by_key.get(key)
            values = values_by_key.get(key, [])
            status = field.status.value if field else "unevaluated"
            checker_status = field.checker_status.value if field else "unevaluated"
            details = "".join(value_card(value) for value in values)
            if not details:
                details = '<p class="empty">Brak zaakceptowanej wartości.</p>'
            notes = ""
            if field and field.notes:
                notes = "<ul>" + "".join(
                    f"<li>{escape(note)}</li>" for note in field.notes
                ) + "</ul>"
            fields.append(
                '<section class="field">'
                f'<div class="field-head"><h3>{escape(target_field)}</h3>'
                f'{badge(status, "bad" if status in {"missing", "unevaluated", "conflicting"} else "ok")}'
                f'{badge(f"checker: {checker_status}")}</div>'
                f'{notes}{details}</section>'
            )
        task_sections.append(
            '<details class="task" open><summary>'
            f'<span>{escape(task.title)}</span>{badge(task.requirement.value)}'
            f'{badge(task.priority.value)}</summary>'
            f'<p class="question">{escape(task.question)}</p>'
            f'{"".join(fields)}</details>'
        )

    source_rows = []
    for source in search_results.sources:
        source_rows.append(
            '<tr>'
            f'<td><code>{escape(source.source_id)}</code></td>'
            f'<td><a href="{escape(str(source.canonical_url), quote=True)}" target="_blank" rel="noopener">{escape(source.title or source.canonical_url)}</a></td>'
            f'<td>{escape(source.source_type.value)}</td>'
            f'<td>{escape(retrieval_by_source.get(source.source_id, "not_retrieved"))}</td>'
            '</tr>'
        )

    warning_items = "".join(f"<li>{escape(item)}</li>" for item in review.warnings)
    c = review.coverage
    decision_label = review.decision.value.replace("_", " ")
    return f"""<!doctype html>
<html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Human Review — {escape(review.brand_name)}</title>
<style>
:root{{--ink:#172033;--muted:#64748b;--line:#dbe3ee;--bg:#f4f7fb;--brand:#3157d5;--ok:#087f5b;--bad:#b42318;--warn:#b45309}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 system-ui,sans-serif}}
main{{max-width:1280px;margin:auto;padding:32px 20px 80px}} h1{{font-size:34px;margin:8px 0}} h2{{margin-top:36px}} h3{{font-size:14px;margin:0}}
.hero,.panel,.task,.field{{background:#fff;border:1px solid var(--line);border-radius:14px}} .hero,.panel{{padding:24px;margin-bottom:18px}}
.eyebrow{{font-weight:800;color:var(--brand);text-transform:uppercase;letter-spacing:.08em}} .muted,.question,.empty{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:18px}} .metric{{background:#f8fafc;border-radius:10px;padding:14px}} .metric strong{{display:block;font-size:24px}}
.badge{{display:inline-block;border-radius:999px;background:#eef2f7;color:#475569;padding:3px 8px;margin-left:6px;font-size:11px;font-weight:800}} .badge.ok{{background:#dcfce7;color:var(--ok)}} .badge.bad{{background:#fee2e2;color:var(--bad)}} .badge.warn{{background:#ffedd5;color:var(--warn)}} .badge.info{{background:#dbeafe;color:#1d4ed8}}
.task{{margin:14px 0;overflow:hidden}} .task>summary{{cursor:pointer;padding:17px;font-weight:850;font-size:17px}} .question{{padding:0 17px}}
.field{{margin:12px 16px;padding:16px;background:#fbfdff}} .field-head,.value-head{{display:flex;align-items:center;gap:5px;flex-wrap:wrap}} .field-head h3{{margin-right:auto}}
.value{{border-top:1px solid var(--line);padding:14px 0}} .value:first-of-type{{border-top:0}} .canonical{{color:#334155}} details summary{{cursor:pointer}} blockquote{{border-left:3px solid #93a4c0;margin:10px 0;padding:8px 12px;background:#f8fafc;white-space:pre-wrap}}
table{{width:100%;border-collapse:collapse}} th,td{{border-bottom:1px solid var(--line);padding:10px;text-align:left;vertical-align:top}} a{{color:#2547b8;overflow-wrap:anywhere}} code{{font-size:12px;overflow-wrap:anywhere}}
@media(max-width:700px){{main{{padding:18px 10px}} .hero,.panel{{padding:16px}} th:nth-child(1),td:nth-child(1){{display:none}}}}
</style></head><body><main>
<section class="hero"><div class="eyebrow">Human Review · {escape(decision_label)}</div><h1>{escape(review.brand_name)}</h1>
<p class="muted">Kraj: {escape(review.target_country)} · iteracja {review.iteration} · jakość Checkera {review.input_quality_score}/{review.input_quality_threshold} · zakres {'kompletny' if review.input_scope_complete else 'niekompletny'}</p>
<div class="grid">
<div class="metric"><strong>{c.evaluated_tasks}/{c.planned_tasks}</strong>zadania ocenione</div>
<div class="metric"><strong>{c.fields_with_values}/{c.planned_fields}</strong>pola z wartością</div>
<div class="metric"><strong>{c.normalized_values}</strong>wartości</div>
<div class="metric"><strong>{c.critical_missing_fields}</strong>krytyczne braki</div>
<div class="metric"><strong>{c.unevaluated_critical_fields}</strong>krytyczne nieocenione</div>
<div class="metric"><strong>{c.sources}</strong>źródła</div></div></section>
<section class="panel"><h2>Decyzja</h2><p><strong>{escape(decision_label)}</strong></p>
<p>Reviewer: {escape(review.reviewer or 'nieprzypisany')}</p><p>{escape(review.reviewer_notes or 'Brak notatki.')}</p></section>
<section class="panel"><h2>Ostrzeżenia i ograniczenia</h2><ul>{warning_items}</ul></section>
<h2>Pola badawcze — zdobyte dane i braki</h2>{''.join(task_sections)}
<section class="panel"><h2>Rejestr źródeł</h2><div style="overflow:auto"><table><thead><tr><th>ID</th><th>Źródło</th><th>Typ</th><th>Pobranie</th></tr></thead><tbody>{''.join(source_rows)}</tbody></table></div></section>
<section class="panel muted"><p>Raport jest widokiem roboczym. Cytaty i statusy pochodzą z niezmiennych artefaktów pipeline’u; decyzja review nie usuwa braków ani nie zmienia werdyktów Checkera.</p>
<p><code>review_id={escape(review.review_id)}</code></p></section>
</main></body></html>"""
