"""Normalizer agent: convert accepted claims into typed staging values."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from ..llm.protocol import NormalizerLLM, NormalizerProviderError
from ..schemas import (
    AgentIterationUsage,
    CheckerFieldStatus,
    CheckerMode,
    CheckerResults,
    CheckerSourceSupport,
    CheckerVerdict,
    ExtractionResults,
    NormalizationPrecision,
    NormalizedValue,
    NormalizedValueType,
    NormalizerAttemptFailure,
    NormalizerDraft,
    NormalizerFieldResult,
    NormalizerFieldStatus,
    NormalizerLimits,
    NormalizerMode,
    NormalizerRepairSummary,
    NormalizerResults,
    NormalizerStrategySource,
    NormalizerValueDraft,
    RawExtractionClaim,
    ResearchPlan,
    SearchResults,
)


DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "normalizer_system_v2.md"
)
DEFAULT_MAX_CLAIMS = 100
DEFAULT_MAX_INPUT_CHARS = 100_000


class NormalizerValidationError(ValueError):
    """Raised before an invalid or misleading Normalizer artifact is saved."""


class NormalizerDraftValidationError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _deterministic_draft(claims: list[RawExtractionClaim]) -> NormalizerDraft:
    return NormalizerDraft(
        values=[
            NormalizerValueDraft(
                task_id=claim.task_id,
                target_field=claim.target_field,
                claim_ids=[claim.claim_id],
                value_type=NormalizedValueType.TEXT,
                canonical_text=claim.value_text,
                precision=NormalizationPrecision.QUALITATIVE,
                unit=claim.unit_text,
                notes="Conservative deterministic text normalization.",
            )
            for claim in claims
        ]
    )


def _normalization_input_chars(
    claims: list[RawExtractionClaim],
    citation_quote_by_id: dict[str, str],
) -> int:
    return sum(
        len(claim.value_text)
        + sum(
            len(value or "")
            for value in (
                claim.asserted_by_text,
                claim.as_of_text,
                claim.unit_text,
                claim.currency_text,
                claim.publication_date_text,
                claim.effective_date_text,
                claim.notes,
            )
        )
        + sum(len(citation_quote_by_id[citation_id]) for citation_id in claim.citation_ids)
        for claim in claims
    )


class NormalizerAgent:
    """Normalize only Checker-accepted facts and preserve exact provenance."""

    def __init__(
        self,
        llm: NormalizerLLM | None = None,
        *,
        prompt_path: Path | str = DEFAULT_PROMPT_PATH,
    ) -> None:
        self.llm = llm
        self.prompt_path = Path(prompt_path)

    def create_normalizer_results(
        self,
        plan: ResearchPlan,
        search_results: SearchResults,
        extraction_results: ExtractionResults,
        checker_results: CheckerResults,
        *,
        plan_sha256: str,
        search_sha256: str,
        extraction_sha256: str,
        check_sha256: str,
        check_reference: str,
        plan_reference: str | None = None,
        search_reference: str | None = None,
        extraction_reference: str | None = None,
        iteration: int | None = None,
        mode: NormalizerMode | None = None,
        allow_incomplete: bool = False,
        max_claims: int = DEFAULT_MAX_CLAIMS,
        max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
    ) -> NormalizerResults:
        resolved_iteration = iteration or checker_results.iteration
        resolved_mode = mode or (
            NormalizerMode.PAID if self.llm is not None else NormalizerMode.FREE
        )
        self._validate_inputs(
            plan,
            search_results,
            extraction_results,
            checker_results,
            plan_sha256=plan_sha256,
            search_sha256=search_sha256,
            extraction_sha256=extraction_sha256,
            check_sha256=check_sha256,
            check_reference=check_reference,
            plan_reference=plan_reference,
            search_reference=search_reference,
            extraction_reference=extraction_reference,
            iteration=resolved_iteration,
            mode=resolved_mode,
            allow_incomplete=allow_incomplete,
            max_claims=max_claims,
            max_input_chars=max_input_chars,
        )

        claim_by_id = {claim.claim_id: claim for claim in extraction_results.claims}
        decision_by_id = {
            decision.claim_id: decision
            for decision in checker_results.claim_decisions
        }
        citation_by_id = {
            citation.citation_id: citation
            for citation in extraction_results.citations
        }
        unsafe_claim_ids = {
            claim_id
            for unsafe_item in checker_results.unsafe_items
            for claim_id in unsafe_item.claim_ids
        }
        eligible_claim_ids = [
            decision.claim_id
            for decision in checker_results.claim_decisions
            if decision.verdict == CheckerVerdict.ACCEPTED
            and decision.claim_id not in unsafe_claim_ids
        ]
        eligible_claims = [claim_by_id[claim_id] for claim_id in eligible_claim_ids]
        excluded_claim_ids = [
            claim_id
            for claim_id in checker_results.selected_claim_ids
            if claim_id not in set(eligible_claim_ids)
        ]
        unsafe_excluded_claim_ids = [
            claim_id
            for claim_id in checker_results.selected_claim_ids
            if claim_id in unsafe_claim_ids
        ]
        observed_input_chars = _normalization_input_chars(
            eligible_claims,
            {
                citation_id: citation.quote
                for citation_id, citation in citation_by_id.items()
            },
        )
        if len(eligible_claims) > max_claims:
            raise NormalizerValidationError(
                f"Normalizer has {len(eligible_claims)} eligible claims but "
                f"max_claims is {max_claims}."
            )
        if observed_input_chars > max_input_chars:
            raise NormalizerValidationError(
                f"Normalizer input has {observed_input_chars} characters but "
                f"max_input_chars is {max_input_chars}."
            )

        deterministic_draft = _deterministic_draft(eligible_claims)
        selected_draft = deterministic_draft
        generated_by = "deterministic"
        strategy_source = NormalizerStrategySource.DETERMINISTIC
        model = None
        provider_executed = False
        usage: list[AgentIterationUsage] = []
        failed_attempts: list[NormalizerAttemptFailure] = []
        repair_summary = NormalizerRepairSummary()
        warnings: list[str] = []

        scope_task_ids, scope_source_ids = self._scope_ids(
            eligible_claims,
            citation_by_id,
        )
        if resolved_mode == NormalizerMode.PAID and eligible_claims:
            if self.llm is None:
                raise NormalizerValidationError(
                    "Paid Normalizer mode requires an LLM client."
                )
            generated_by = "openai"
            model = self.llm.model_name
            provider_executed = True
            try:
                system_prompt = self.prompt_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise NormalizerValidationError(
                    f"Cannot load Normalizer prompt: {self.prompt_path}"
                ) from exc
            try:
                generation = self.llm.generate(
                    plan,
                    search_results,
                    extraction_results,
                    checker_results,
                    eligible_claim_ids,
                    system_prompt,
                    iteration=resolved_iteration,
                    call_index=1,
                )
                usage.append(generation.usage)
                self._validate_usage(
                    generation.usage,
                    iteration=resolved_iteration,
                    scope_task_ids=scope_task_ids,
                    scope_source_ids=scope_source_ids,
                )
                selected_draft, repair_summary = self._prepare_draft(
                    generation.draft,
                    eligible_claims,
                )
            except NormalizerProviderError as exc:
                if exc.usage is not None:
                    self._validate_usage(
                        exc.usage,
                        iteration=resolved_iteration,
                        scope_task_ids=scope_task_ids,
                        scope_source_ids=scope_source_ids,
                    )
                    usage.append(exc.usage)
                failed_attempts.append(
                    NormalizerAttemptFailure(
                        scope_task_ids=scope_task_ids,
                        scope_source_ids=scope_source_ids,
                        error_code=exc.code,
                        usage_recorded=exc.usage is not None,
                        token_usage_unknown=exc.usage is None,
                    )
                )
                strategy_source = NormalizerStrategySource.DETERMINISTIC_FALLBACK
                warnings.append(
                    f"Paid Normalizer failed with {exc.code}; retained conservative "
                    "deterministic text values."
                )
            except NormalizerDraftValidationError as exc:
                failed_attempts.append(
                    NormalizerAttemptFailure(
                        scope_task_ids=scope_task_ids,
                        scope_source_ids=scope_source_ids,
                        error_code=exc.code,
                        usage_recorded=True,
                    )
                )
                strategy_source = NormalizerStrategySource.DETERMINISTIC_FALLBACK
                warnings.append(
                    f"Paid Normalizer draft failed local validation ({exc.code}); "
                    "retained conservative deterministic text values."
                )
            else:
                if repair_summary.repaired_provider_value_groups:
                    strategy_source = NormalizerStrategySource.OPENAI_REPAIRED
                    issue_codes = ", ".join(repair_summary.issue_codes)
                    warnings.append(
                        "Repaired "
                        f"{repair_summary.repaired_provider_value_groups} invalid "
                        "provider value group(s) with "
                        f"{repair_summary.deterministic_replacement_values} "
                        "deterministic text value(s); local rule code(s): "
                        f"{issue_codes}."
                    )
                else:
                    strategy_source = NormalizerStrategySource.OPENAI
                if generation.draft.warnings:
                    warnings.append(
                        f"Discarded {len(generation.draft.warnings)} provider-authored "
                        "warning string(s); model prose is not evidence."
                    )
        elif resolved_mode == NormalizerMode.PAID:
            warnings.append(
                "No accepted claims were eligible; the paid Normalizer made no API call."
            )

        normalized_values = self._materialize_values(
            selected_draft,
            claim_by_id,
            decision_by_id,
            citation_by_id,
        )
        field_results, ignored_contradictions = self._build_field_results(
            checker_results,
            normalized_values,
            eligible_claim_ids=eligible_claim_ids,
            unsafe_excluded_claim_ids=unsafe_excluded_claim_ids,
        )
        if ignored_contradictions:
            warnings.append(
                f"Ignored {ignored_contradictions} Checker contradiction(s) involving "
                "claims that were not eligible for normalization."
            )
        if not checker_results.passed:
            warnings.append(
                f"Input Checker did not pass ({checker_results.quality_score}/"
                f"{checker_results.quality_threshold}); normalized data is an "
                "incomplete review draft created under explicit override."
            )
        if not checker_results.scope_complete:
            warnings.append(
                f"Research scope is incomplete: {len(checker_results.unevaluated_task_ids)} "
                "plan task(s) remain unevaluated."
            )
        if unsafe_excluded_claim_ids:
            warnings.append(
                f"Excluded {len(unsafe_excluded_claim_ids)} accepted claim(s) linked "
                "to Checker safety findings."
            )
        warnings.append(
            "Normalizer output is a staging artifact and cannot be published before "
            "explicit human approval."
        )
        warnings = _deduplicate(warnings)
        unresolved_field_ids = [
            field.normalized_field_id
            for field in field_results
            if field.checker_status != CheckerFieldStatus.VERIFIED
            or field.status
            in {
                NormalizerFieldStatus.CONFLICTING,
                NormalizerFieldStatus.NEEDS_REVIEW,
                NormalizerFieldStatus.MISSING,
            }
        ]
        return NormalizerResults(
            normalization_id=str(uuid4()),
            plan_run_id=plan.run_id,
            search_id=search_results.search_id,
            extraction_id=extraction_results.extraction_id,
            check_id=checker_results.check_id,
            plan_sha256=plan_sha256,
            search_sha256=search_sha256,
            extraction_sha256=extraction_sha256,
            check_sha256=check_sha256,
            plan_reference=plan_reference or checker_results.plan_reference,
            search_reference=search_reference or checker_results.search_reference,
            extraction_reference=(
                extraction_reference or checker_results.extraction_reference
            ),
            check_reference=check_reference,
            created_at=datetime.now(timezone.utc),
            iteration=resolved_iteration,
            normalization_mode=resolved_mode,
            generated_by=generated_by,
            strategy_source=strategy_source,
            model=model,
            provider_executed=provider_executed,
            brand_name=plan.planner_input.brand_name,
            target_country=plan.planner_input.target_country,
            depth=plan.planner_input.depth,
            input_checker_passed=checker_results.passed,
            incomplete_input_allowed=allow_incomplete,
            input_quality_score=checker_results.quality_score,
            input_quality_threshold=checker_results.quality_threshold,
            input_scope_complete=checker_results.scope_complete,
            limits=NormalizerLimits(
                max_claims=max_claims,
                max_input_chars=max_input_chars,
                observed_input_chars=observed_input_chars,
            ),
            repair_summary=repair_summary,
            eligible_claim_ids=eligible_claim_ids,
            excluded_claim_ids=excluded_claim_ids,
            unsafe_excluded_claim_ids=unsafe_excluded_claim_ids,
            normalized_values=normalized_values,
            field_results=field_results,
            unresolved_field_ids=unresolved_field_ids,
            critical_missing_fields=checker_results.critical_missing_fields,
            unevaluated_critical_fields=checker_results.unevaluated_critical_fields,
            warnings=warnings,
            compliance_rules=_deduplicate(
                [
                    *checker_results.compliance_rules,
                    "Normalize only accepted, grounded Checker claims.",
                    "Preserve claim, citation, and source provenance for every value.",
                    "Never publish or import Normalizer output without human approval.",
                ]
            ),
            agent_usage=usage,
            failed_attempts=failed_attempts,
        )

    @staticmethod
    def _scope_ids(claims, citation_by_id) -> tuple[list[str], list[str]]:
        return (
            _deduplicate([claim.task_id for claim in claims]),
            _deduplicate(
                [
                    citation_by_id[citation_id].source_id
                    for claim in claims
                    for citation_id in claim.citation_ids
                ]
            ),
        )

    @staticmethod
    def _validate_usage(
        usage: AgentIterationUsage,
        *,
        iteration: int,
        scope_task_ids: list[str],
        scope_source_ids: list[str],
    ) -> None:
        if (
            usage.agent != "normalizer"
            or usage.iteration != iteration
            or usage.call_index != 1
            or usage.scope_task_ids != scope_task_ids
            or usage.scope_source_ids != scope_source_ids
        ):
            raise NormalizerDraftValidationError(
                "Normalizer provider usage scope is inconsistent.",
                code="invalid_usage_scope",
            )

    @staticmethod
    def _prepare_draft(
        draft: NormalizerDraft,
        eligible_claims: list[RawExtractionClaim],
    ) -> tuple[NormalizerDraft, NormalizerRepairSummary]:
        if len(draft.values) > 500:
            raise NormalizerDraftValidationError(
                "Normalizer draft contains too many value groups.",
                code="too_many_value_groups",
            )
        claim_by_id = {claim.claim_id: claim for claim in eligible_claims}
        draft_claim_ids = [
            claim_id for value in draft.values for claim_id in value.claim_ids
        ]
        if len(draft_claim_ids) != len(set(draft_claim_ids)):
            raise NormalizerDraftValidationError(
                "Normalizer draft reused a claim ID.",
                code="duplicate_claim_assignment",
            )
        if set(draft_claim_ids) != set(claim_by_id):
            raise NormalizerDraftValidationError(
                "Normalizer draft did not cover the exact accepted claim set.",
                code="invalid_claim_coverage",
            )
        for value in draft.values:
            if not value.claim_ids:
                raise NormalizerDraftValidationError(
                    "Normalizer draft contains an empty claim group.",
                    code="invalid_claim_grouping",
                )
            claims = [claim_by_id[claim_id] for claim_id in value.claim_ids]
            if any(
                claim.task_id != value.task_id
                or claim.target_field != value.target_field
                for claim in claims
            ):
                raise NormalizerDraftValidationError(
                    "Normalizer grouped claims from different tasks or fields.",
                    code="invalid_claim_grouping",
                )

        repaired_values: list[NormalizerValueDraft] = []
        issue_codes: list[str] = []
        repaired_groups = 0
        deterministic_replacements = 0
        for value in draft.values:
            issue_code = value.semantic_issue_code()
            if issue_code is None:
                repaired_values.append(value)
                continue
            repaired_groups += 1
            issue_codes.append(issue_code)
            replacements = _deterministic_draft(
                [claim_by_id[claim_id] for claim_id in value.claim_ids]
            ).values
            deterministic_replacements += len(replacements)
            repaired_values.extend(replacements)

        return (
            NormalizerDraft(values=repaired_values, warnings=draft.warnings),
            NormalizerRepairSummary(
                provider_value_groups=len(draft.values),
                accepted_provider_value_groups=len(draft.values) - repaired_groups,
                repaired_provider_value_groups=repaired_groups,
                deterministic_replacement_values=deterministic_replacements,
                issue_codes=_deduplicate(issue_codes),
            ),
        )

    @staticmethod
    def _materialize_values(
        draft: NormalizerDraft,
        claim_by_id,
        decision_by_id,
        citation_by_id,
    ) -> list[NormalizedValue]:
        values: list[NormalizedValue] = []
        for item in draft.values:
            claims = [claim_by_id[claim_id] for claim_id in item.claim_ids]
            citation_ids = _deduplicate(
                [
                    citation_id
                    for claim in claims
                    for citation_id in claim.citation_ids
                ]
            )
            source_ids = _deduplicate(
                [citation_by_id[citation_id].source_id for citation_id in citation_ids]
            )
            values.append(
                NormalizedValue(
                    normalized_value_id=_stable_id(
                        "normalized-value",
                        item.task_id,
                        item.target_field,
                        *sorted(item.claim_ids),
                    ),
                    task_id=item.task_id,
                    target_field=item.target_field,
                    claim_ids=item.claim_ids,
                    value_type=item.value_type,
                    canonical_text=item.canonical_text,
                    number_min=(
                        Decimal(item.number_min)
                        if item.number_min is not None
                        else None
                    ),
                    number_max=(
                        Decimal(item.number_max)
                        if item.number_max is not None
                        else None
                    ),
                    boolean_value=item.boolean_value,
                    date_value=(
                        date.fromisoformat(item.date_value)
                        if item.date_value is not None
                        else None
                    ),
                    currency=item.currency,
                    unit=item.unit,
                    precision=item.precision,
                    notes=item.notes,
                    raw_value_texts=_deduplicate(
                        [claim.value_text for claim in claims]
                    ),
                    citation_ids=citation_ids,
                    source_ids=source_ids,
                    needs_corroboration=any(
                        decision_by_id[claim.claim_id].source_support
                        == CheckerSourceSupport.NEEDS_CORROBORATION
                        for claim in claims
                    ),
                )
            )
        return values

    @staticmethod
    def _build_field_results(
        checker_results: CheckerResults,
        normalized_values: list[NormalizedValue],
        *,
        eligible_claim_ids: list[str],
        unsafe_excluded_claim_ids: list[str],
    ) -> tuple[list[NormalizerFieldResult], int]:
        eligible_set = set(eligible_claim_ids)
        unsafe_set = set(unsafe_excluded_claim_ids)
        values_by_key: dict[tuple[str, str], list[NormalizedValue]] = {}
        for value in normalized_values:
            values_by_key.setdefault((value.task_id, value.target_field), []).append(
                value
            )
        conflicting_keys: set[tuple[str, str]] = set()
        ignored_contradictions = 0
        for contradiction in checker_results.contradictions:
            if set(contradiction.claim_ids).issubset(eligible_set):
                conflicting_keys.add(
                    (contradiction.task_id, contradiction.target_field)
                )
            else:
                ignored_contradictions += 1
        fields: list[NormalizerFieldResult] = []
        for task_result in checker_results.task_results:
            for field in task_result.field_results:
                key = (task_result.task_id, field.target_field)
                values = values_by_key.get(key, [])
                eligible_for_field = [
                    claim_id
                    for claim_id in field.accepted_claim_ids
                    if claim_id in eligible_set
                ]
                needs_review_ids = _deduplicate(
                    [
                        *field.needs_review_claim_ids,
                        *(
                            claim_id
                            for claim_id in field.accepted_claim_ids
                            if claim_id in unsafe_set
                        ),
                    ]
                )
                if field.audit_basis is not None:
                    status = NormalizerFieldStatus.DERIVED
                elif key in conflicting_keys:
                    status = NormalizerFieldStatus.CONFLICTING
                elif eligible_for_field and needs_review_ids:
                    status = NormalizerFieldStatus.NEEDS_REVIEW
                elif len(values) == 1:
                    status = NormalizerFieldStatus.NORMALIZED
                elif len(values) > 1:
                    status = NormalizerFieldStatus.MULTIPLE_VALUES
                elif needs_review_ids:
                    status = NormalizerFieldStatus.NEEDS_REVIEW
                else:
                    status = NormalizerFieldStatus.MISSING
                notes: list[str] = []
                if field.audit_basis is not None:
                    notes.append(field.audit_basis)
                if any(value.needs_corroboration for value in values):
                    notes.append("One or more accepted values still need corroboration.")
                if key in conflicting_keys:
                    notes.append("Accepted claims retain an unresolved contradiction.")
                if any(claim_id in unsafe_set for claim_id in field.accepted_claim_ids):
                    notes.append("Safety-linked accepted claims were excluded.")
                fields.append(
                    NormalizerFieldResult(
                        normalized_field_id=_stable_id(
                            "normalized-field", task_result.task_id, field.target_field
                        ),
                        task_id=task_result.task_id,
                        target_field=field.target_field,
                        checker_status=field.status,
                        status=status,
                        normalized_value_ids=[
                            value.normalized_value_id for value in values
                        ],
                        accepted_claim_ids=eligible_for_field,
                        rejected_claim_ids=field.rejected_claim_ids,
                        needs_review_claim_ids=needs_review_ids,
                        source_ids=_deduplicate(
                            [source_id for value in values for source_id in value.source_ids]
                        ),
                        notes=notes,
                    )
                )
        return fields, ignored_contradictions

    def _validate_inputs(
        self,
        plan: ResearchPlan,
        search_results: SearchResults,
        extraction_results: ExtractionResults,
        checker_results: CheckerResults,
        *,
        plan_sha256: str,
        search_sha256: str,
        extraction_sha256: str,
        check_sha256: str,
        check_reference: str,
        plan_reference: str | None,
        search_reference: str | None,
        extraction_reference: str | None,
        iteration: int,
        mode: NormalizerMode,
        allow_incomplete: bool,
        max_claims: int,
        max_input_chars: int,
    ) -> None:
        for value, label in (
            (plan_sha256, "Plan"),
            (search_sha256, "Searcher"),
            (extraction_sha256, "Extractor"),
            (check_sha256, "Checker"),
        ):
            if not re.fullmatch(r"[a-f0-9]{64}", value):
                raise NormalizerValidationError(
                    f"{label} artifact SHA-256 must be a lowercase hexadecimal digest."
                )
        if not check_reference.strip():
            raise NormalizerValidationError("Checker artifact reference cannot be blank.")
        if iteration < 1:
            raise NormalizerValidationError("Normalizer iteration must be positive.")
        if not 1 <= max_claims <= 500:
            raise NormalizerValidationError("max_claims must be between 1 and 500.")
        if not 1_000 <= max_input_chars <= 500_000:
            raise NormalizerValidationError(
                "max_input_chars must be between 1000 and 500000."
            )
        if mode == NormalizerMode.FREE and self.llm is not None:
            raise NormalizerValidationError("Free Normalizer cannot use an LLM client.")
        if mode == NormalizerMode.PAID and self.llm is not None and not self.llm.model_name.strip():
            raise NormalizerValidationError("Paid Normalizer model name cannot be blank.")
        if checker_results.generated_by != "openai" or checker_results.failed_attempts:
            raise NormalizerValidationError(
                "Normalizer requires a successful paid Checker artifact."
            )
        if checker_results.checker_mode != CheckerMode.FULL:
            raise NormalizerValidationError(
                "Normalizer requires a full Checker artifact; incremental judgments "
                "must be followed by a full paid Checker pass."
            )
        if not checker_results.passed and not allow_incomplete:
            raise NormalizerValidationError(
                "Checker did not pass. Review its documented gaps and rerun with "
                "--allow-incomplete only when an incomplete staging draft is intended."
            )
        if (
            search_results.plan_run_id != plan.run_id
            or extraction_results.plan_run_id != plan.run_id
            or checker_results.plan_run_id != plan.run_id
        ):
            raise NormalizerValidationError("Normalizer lineage has mismatched run IDs.")
        if (
            extraction_results.search_id != search_results.search_id
            or checker_results.search_id != search_results.search_id
            or checker_results.extraction_id != extraction_results.extraction_id
        ):
            raise NormalizerValidationError("Normalizer lineage has mismatched artifact IDs.")
        if (
            search_results.plan_sha256 != plan_sha256
            or extraction_results.plan_sha256 != plan_sha256
            or checker_results.plan_sha256 != plan_sha256
            or extraction_results.search_sha256 != search_sha256
            or checker_results.search_sha256 != search_sha256
            or checker_results.extraction_sha256 != extraction_sha256
        ):
            raise NormalizerValidationError(
                "Normalizer lineage does not match exact artifact bytes."
            )
        for supplied, recorded, label in (
            (plan_reference, checker_results.plan_reference, "Plan"),
            (search_reference, checker_results.search_reference, "Searcher"),
            (extraction_reference, checker_results.extraction_reference, "Extractor"),
        ):
            if supplied is not None and supplied != recorded:
                raise NormalizerValidationError(
                    f"Checker {label} reference does not match supplied path."
                )
        expected_metadata = (
            plan.planner_input.brand_name,
            plan.planner_input.target_country,
            plan.planner_input.depth,
        )
        if any(
            metadata != expected_metadata
            for metadata in (
                (
                    search_results.brand_name,
                    search_results.target_country,
                    search_results.depth,
                ),
                (
                    extraction_results.brand_name,
                    extraction_results.target_country,
                    extraction_results.depth,
                ),
                (
                    checker_results.brand_name,
                    checker_results.target_country,
                    checker_results.depth,
                ),
            )
        ):
            raise NormalizerValidationError(
                "Normalizer inputs have inconsistent brand/country/depth metadata."
            )
        claim_by_id = {claim.claim_id: claim for claim in extraction_results.claims}
        if any(claim_id not in claim_by_id for claim_id in checker_results.selected_claim_ids):
            raise NormalizerValidationError("Checker references unknown Extractor claims.")
        citation_ids = {citation.citation_id for citation in extraction_results.citations}
        if any(
            citation_id not in citation_ids
            for claim_id in checker_results.selected_claim_ids
            for citation_id in claim_by_id[claim_id].citation_ids
        ):
            raise NormalizerValidationError("Accepted claim citation lineage is incomplete.")
