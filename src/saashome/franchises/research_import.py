"""Import an approved Human Review artifact into auditable Django models."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils.text import slugify


REPOSITORY_ROOT = settings.BASE_DIR.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from datacollector.schemas import (  # noqa: E402
    HumanReviewDecision,
    NormalizerMode,
    NormalizerStrategySource,
)
from datacollector.storage.json_store import (  # noqa: E402
    load_checker_results,
    load_extraction_results,
    load_human_review_results,
    load_normalizer_results,
    load_research_plan,
    load_search_results,
)

from .models import (  # noqa: E402
    Franchise,
    FranchiseCategory,
    FranchiseResearchArtifact,
    FranchiseResearchCitation,
    FranchiseResearchClaim,
    FranchiseResearchClaimCitation,
    FranchiseResearchField,
    FranchiseResearchImport,
    FranchiseResearchSource,
    FranchiseResearchTask,
    FranchiseResearchValue,
    FranchiseResearchValueClaim,
)


class FranchiseResearchImportError(ValueError):
    """Raised before an unsafe or inconsistent import mutates the database."""


def _resolved(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    working_directory_candidate = (Path.cwd() / candidate).resolve()
    if working_directory_candidate.exists():
        return working_directory_candidate

    # Management commands are commonly invoked from settings.BASE_DIR
    # (src/saashome), while datacollector prints paths relative to the repository
    # root. Accept both forms without weakening the lineage/hash validation.
    repository_candidate = (REPOSITORY_ROOT / candidate).resolve()
    if repository_candidate.exists():
        return repository_candidate

    # Preserve the conventional current-working-directory error location when
    # neither candidate exists, so CommandError remains easy to diagnose.
    return working_directory_candidate


def _artifact_metadata(value, external_id: str) -> tuple[str, str, str]:
    return (
        external_id,
        str(getattr(value, "schema_version", "")),
        str(getattr(value, "prompt_version", "")),
    )


def _optional_text(value) -> str:
    return "" if value is None else str(value)


def _load_approved_lineage(review_path: Path, *, allow_approved_with_gaps: bool):
    review, review_sha256 = load_human_review_results(review_path)
    if not review.approved_for_import:
        raise FranchiseResearchImportError(
            f"Human Review decision {review.decision.value!r} does not authorize import."
        )
    if (
        review.decision == HumanReviewDecision.APPROVED_WITH_GAPS
        and not allow_approved_with_gaps
    ):
        raise FranchiseResearchImportError(
            "approved_with_gaps requires --allow-approved-with-gaps."
        )
    normalized_path = _resolved(review.normalized_reference)
    normalized, normalized_sha256 = load_normalizer_results(normalized_path)
    if (
        normalized_sha256 != review.normalized_sha256
        or normalized.normalization_id != review.normalization_id
    ):
        raise FranchiseResearchImportError(
            "Normalizer bytes or ID do not match the approved Human Review."
        )
    if normalized.normalization_mode != NormalizerMode.PAID or (
        normalized.strategy_source
        not in {
            NormalizerStrategySource.OPENAI,
            NormalizerStrategySource.OPENAI_REPAIRED,
        }
    ):
        raise FranchiseResearchImportError(
            "Importer requires a successful paid Normalizer artifact."
        )
    report_path = _resolved(review.report_reference)
    if not report_path.is_file():
        raise FranchiseResearchImportError("Human Review report is unavailable.")

    plan_path = _resolved(normalized.plan_reference)
    search_path = _resolved(normalized.search_reference)
    extraction_path = _resolved(normalized.extraction_reference)
    check_path = _resolved(normalized.check_reference)
    plan, plan_sha256 = load_research_plan(plan_path)
    search, search_sha256 = load_search_results(search_path)
    extraction, extraction_sha256 = load_extraction_results(extraction_path)
    checker, check_sha256 = load_checker_results(check_path)
    for actual, expected, label in (
        (plan_sha256, normalized.plan_sha256, "Plan"),
        (search_sha256, normalized.search_sha256, "Searcher"),
        (extraction_sha256, normalized.extraction_sha256, "Extractor"),
        (check_sha256, normalized.check_sha256, "Checker"),
    ):
        if actual != expected:
            raise FranchiseResearchImportError(
                f"{label} bytes do not match approved Normalizer lineage."
            )
    return {
        "review": review,
        "review_sha256": review_sha256,
        "review_path": review_path,
        "normalized": normalized,
        "normalized_sha256": normalized_sha256,
        "normalized_path": normalized_path,
        "plan": plan,
        "plan_sha256": plan_sha256,
        "plan_path": plan_path,
        "search": search,
        "search_sha256": search_sha256,
        "search_path": search_path,
        "extraction": extraction,
        "extraction_sha256": extraction_sha256,
        "extraction_path": extraction_path,
        "checker": checker,
        "check_sha256": check_sha256,
        "check_path": check_path,
    }


def _get_or_create_franchise(
    brand_name: str,
    *,
    franchise_slug: str | None,
    category_slug: str,
) -> tuple[Franchise, bool]:
    resolved_slug = franchise_slug or slugify(brand_name) or "research-franchise"
    franchise = Franchise.objects.filter(slug=resolved_slug).first()
    if franchise is not None:
        return franchise, False
    category, _ = FranchiseCategory.objects.get_or_create(
        slug=category_slug,
        defaults={"name": "Pozostałe", "is_active": True},
    )
    return Franchise.objects.create(
        name=brand_name[:180],
        slug=resolved_slug[:200],
        category=category,
        short_description=(
            "Profil utworzony z zatwierdzonego, audytowalnego researchu. "
            "Szczegóły i braki znajdują się w raporcie danych."
        ),
        data_status=Franchise.DATA_STATUS_RESEARCH_WITH_GAPS,
        is_verified=False,
        is_active=True,
    ), True


def _apply_safe_profile_values(franchise: Franchise, research_import) -> None:
    values_by_field: dict[str, list[FranchiseResearchValue]] = {}
    for value in research_import.values.select_related("field"):
        if value.field.status != "normalized":
            continue
        values_by_field.setdefault(value.field.target_field, []).append(value)

    updates: set[str] = set()

    def one(field_name: str):
        values = values_by_field.get(field_name, [])
        return values[0] if len(values) == 1 else None

    brand = one("brand.name")
    if brand and brand.canonical_text.strip():
        franchise.name = brand.canonical_text.strip()[:180]
        updates.add("name")
    website = one("websites.official") or one("websites.franchise_offer")
    if website and website.canonical_text.startswith(("http://", "https://")):
        franchise.website_url = website.canonical_text[:200]
        updates.add("website_url")
    mapping = {
        "investment.total_low": "min_investment",
        "investment.total_high": "max_investment",
        "fees.initial": "initial_fee",
    }
    for target_field, model_field in mapping.items():
        value = one(target_field)
        if value and value.number_min_text:
            setattr(franchise, model_field, value.number_min_text)
            updates.add(model_field)
    for target_field, model_field in (
        ("fees.royalty", "royalty_fee_text"),
        ("fees.marketing", "marketing_fee_text"),
    ):
        value = one(target_field)
        if value:
            setattr(franchise, model_field, value.canonical_text[:160])
            updates.add(model_field)
    financing = one("financing.available")
    if financing and financing.boolean_value is not None:
        franchise.financing_available = financing.boolean_value
        updates.add("financing_available")

    if research_import.decision == FranchiseResearchImport.DECISION_APPROVED:
        franchise.data_status = Franchise.DATA_STATUS_RESEARCH_REVIEWED
        franchise.is_verified = bool(
            research_import.checker_passed and research_import.scope_complete
        )
    else:
        franchise.data_status = Franchise.DATA_STATUS_RESEARCH_WITH_GAPS
        franchise.is_verified = False
    updates.update({"data_status", "is_verified"})
    franchise.save(update_fields=sorted(updates | {"updated_at"}))


@transaction.atomic
def import_franchise_research(
    review_path: str | Path,
    *,
    franchise_slug: str | None = None,
    category_slug: str = "pozostale",
    allow_approved_with_gaps: bool = False,
) -> tuple[FranchiseResearchImport, bool]:
    review_path = _resolved(review_path)
    lineage = _load_approved_lineage(
        review_path,
        allow_approved_with_gaps=allow_approved_with_gaps,
    )
    review = lineage["review"]
    normalized = lineage["normalized"]
    existing = FranchiseResearchImport.objects.filter(
        normalization_id=normalized.normalization_id
    ).select_related("franchise").first()
    if existing is not None:
        if str(existing.review_id) != review.review_id:
            raise FranchiseResearchImportError(
                "This Normalizer artifact was already imported under another review."
            )
        return existing, False

    franchise, _ = _get_or_create_franchise(
        review.brand_name,
        franchise_slug=franchise_slug,
        category_slug=category_slug,
    )
    FranchiseResearchImport.objects.filter(
        franchise=franchise,
        is_current=True,
    ).update(is_current=False)
    research_import = FranchiseResearchImport.objects.create(
        franchise=franchise,
        review_id=review.review_id,
        normalization_id=review.normalization_id,
        plan_run_id=normalized.plan_run_id,
        search_id=normalized.search_id,
        extraction_id=normalized.extraction_id,
        check_id=normalized.check_id,
        target_country=review.target_country,
        depth=review.depth.value,
        decision=review.decision.value,
        reviewer=review.reviewer or "",
        reviewer_notes=review.reviewer_notes,
        incomplete_input_acknowledged=review.incomplete_input_acknowledged,
        checker_passed=review.input_checker_passed,
        scope_complete=review.input_scope_complete,
        quality_score=review.input_quality_score,
        quality_threshold=review.input_quality_threshold,
        planned_tasks=review.coverage.planned_tasks,
        evaluated_tasks=review.coverage.evaluated_tasks,
        planned_fields=review.coverage.planned_fields,
        evaluated_fields=review.coverage.evaluated_fields,
        normalized_values_count=review.coverage.normalized_values,
        source_count=review.coverage.sources,
        claim_count=len(lineage["extraction"].claims),
        citation_count=review.coverage.citations,
        review_reference=str(review_path),
        review_sha256=lineage["review_sha256"],
        normalized_reference=str(lineage["normalized_path"]),
        normalized_sha256=lineage["normalized_sha256"],
        is_current=True,
    )

    artifact_specs = (
        ("plan", lineage["plan"], lineage["plan"].run_id, lineage["plan_path"], lineage["plan_sha256"]),
        ("search", lineage["search"], lineage["search"].search_id, lineage["search_path"], lineage["search_sha256"]),
        ("extraction", lineage["extraction"], lineage["extraction"].extraction_id, lineage["extraction_path"], lineage["extraction_sha256"]),
        ("check", lineage["checker"], lineage["checker"].check_id, lineage["check_path"], lineage["check_sha256"]),
        ("normalization", normalized, normalized.normalization_id, lineage["normalized_path"], lineage["normalized_sha256"]),
        ("review", review, review.review_id, review_path, lineage["review_sha256"]),
    )
    for artifact_type, artifact, external_id, reference, digest in artifact_specs:
        external_id, schema_version, prompt_version = _artifact_metadata(
            artifact, str(external_id)
        )
        FranchiseResearchArtifact.objects.create(
            research_import=research_import,
            artifact_type=artifact_type,
            external_id=external_id,
            schema_version=schema_version,
            prompt_version=prompt_version,
            reference=str(reference),
            sha256=digest,
            payload=artifact.model_dump(mode="json"),
        )

    checker_task_by_id = {
        item.task_id: item for item in lineage["checker"].task_results
    }
    normalized_field_by_key = {
        (item.task_id, item.target_field): item
        for item in normalized.field_results
    }
    field_by_key = {}
    for position, task in enumerate(lineage["plan"].tasks):
        checker_task = checker_task_by_id.get(task.task_id)
        task_record = FranchiseResearchTask.objects.create(
            research_import=research_import,
            task_id=task.task_id,
            catalog_question_id=task.catalog_question_id,
            section_id=task.section_id,
            title=task.title,
            question=task.question,
            requirement=task.requirement.value,
            priority=task.priority.value,
            status=checker_task.status.value if checker_task else "unevaluated",
            is_evaluated=checker_task is not None,
            sort_order=position,
            raw_payload=task.model_dump(mode="json"),
        )
        for target_field in task.target_fields:
            normalized_field = normalized_field_by_key.get(
                (task.task_id, target_field)
            )
            field_record = FranchiseResearchField.objects.create(
                task=task_record,
                target_field=target_field,
                requirement=task.requirement.value,
                priority=task.priority.value,
                status=(normalized_field.status.value if normalized_field else "unevaluated"),
                checker_status=(
                    normalized_field.checker_status.value
                    if normalized_field
                    else "unevaluated"
                ),
                is_evaluated=normalized_field is not None,
                is_critical=(task.requirement.value == "critical"),
                normalized_field_id=(
                    normalized_field.normalized_field_id if normalized_field else ""
                ),
                accepted_claim_ids=(
                    normalized_field.accepted_claim_ids if normalized_field else []
                ),
                needs_review_claim_ids=(
                    normalized_field.needs_review_claim_ids if normalized_field else []
                ),
                rejected_claim_ids=(
                    normalized_field.rejected_claim_ids if normalized_field else []
                ),
                notes=normalized_field.notes if normalized_field else [],
            )
            field_by_key[(task.task_id, target_field)] = field_record

    document_by_source = {
        document.source_id: document for document in lineage["extraction"].documents
    }
    source_by_id = {}
    for source in lineage["search"].sources:
        document = document_by_source.get(source.source_id)
        source_by_id[source.source_id] = FranchiseResearchSource.objects.create(
            research_import=research_import,
            source_id=source.source_id,
            canonical_url=source.canonical_url,
            title=source.title,
            source_type=source.source_type.value,
            origin=source.origin.value,
            provider_observed=source.provider_observed,
            retrieval_status=(document.retrieval_status.value if document else ""),
            task_ids=source.task_ids,
            raw_payload=source.model_dump(mode="json"),
        )

    decision_by_claim = {
        item.claim_id: item for item in lineage["checker"].claim_decisions
    }
    eligible_ids = set(normalized.eligible_claim_ids)
    excluded_ids = set(normalized.excluded_claim_ids)
    claim_by_id = {}
    for claim in lineage["extraction"].claims:
        decision = decision_by_claim.get(claim.claim_id)
        claim_by_id[claim.claim_id] = FranchiseResearchClaim.objects.create(
            research_import=research_import,
            field=field_by_key.get((claim.task_id, claim.target_field)),
            claim_id=claim.claim_id,
            task_id=claim.task_id,
            target_field=claim.target_field,
            value_text=claim.value_text,
            asserted_by_text=_optional_text(claim.asserted_by_text),
            as_of_text=_optional_text(claim.as_of_text),
            unit_text=_optional_text(claim.unit_text),
            currency_text=_optional_text(claim.currency_text),
            publication_date_text=_optional_text(claim.publication_date_text),
            effective_date_text=_optional_text(claim.effective_date_text),
            notes=claim.notes,
            checker_verdict=decision.verdict.value if decision else "unevaluated",
            semantic_fit=decision.semantic_fit.value if decision else "",
            source_support=decision.source_support.value if decision else "",
            issue_codes=decision.issue_codes if decision else [],
            is_eligible=claim.claim_id in eligible_ids,
            is_excluded=claim.claim_id in excluded_ids,
            raw_payload=claim.model_dump(mode="json"),
        )

    citation_by_id = {}
    for citation in lineage["extraction"].citations:
        citation_by_id[citation.citation_id] = FranchiseResearchCitation.objects.create(
            research_import=research_import,
            source=source_by_id.get(citation.source_id),
            citation_id=citation.citation_id,
            passage_id=citation.passage_id,
            document_id=citation.document_id,
            quote=citation.quote,
            locator=citation.locator or "",
            text_sha256=citation.text_sha256,
            start_char=citation.start_char,
            end_char=citation.end_char,
            raw_payload=citation.model_dump(mode="json"),
        )
    for claim in lineage["extraction"].claims:
        for citation_id in claim.citation_ids:
            if citation_id in citation_by_id:
                FranchiseResearchClaimCitation.objects.create(
                    claim=claim_by_id[claim.claim_id],
                    citation=citation_by_id[citation_id],
                )

    for value in normalized.normalized_values:
        field = field_by_key[(value.task_id, value.target_field)]
        value_record = FranchiseResearchValue.objects.create(
            research_import=research_import,
            field=field,
            normalized_value_id=value.normalized_value_id,
            value_type=value.value_type.value,
            canonical_text=value.canonical_text,
            number_min_text=_optional_text(value.number_min),
            number_max_text=_optional_text(value.number_max),
            boolean_value=value.boolean_value,
            date_value=value.date_value,
            currency=value.currency or "",
            unit=value.unit or "",
            precision=value.precision.value,
            notes=value.notes,
            raw_value_texts=value.raw_value_texts,
            citation_ids=value.citation_ids,
            source_ids=value.source_ids,
            needs_corroboration=value.needs_corroboration,
        )
        for claim_id in value.claim_ids:
            FranchiseResearchValueClaim.objects.create(
                value=value_record,
                claim=claim_by_id[claim_id],
            )

    _apply_safe_profile_values(franchise, research_import)
    return research_import, True
