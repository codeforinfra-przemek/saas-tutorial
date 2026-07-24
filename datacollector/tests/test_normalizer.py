import json
from collections import defaultdict
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from datacollector.agents.checker import CheckerAgent
from datacollector.agents.normalizer import (
    NormalizerAgent,
    NormalizerValidationError,
    _bounded_low_risk_claim_ids,
)
from datacollector.cli import main
from datacollector.llm.protocol import (
    NormalizerGeneration,
    NormalizerProviderError,
)
from datacollector.schemas import (
    AgentIterationUsage,
    CheckerClaimDecisionDraft,
    CheckerContradiction,
    CheckerContradictionKind,
    CheckerFieldStatus,
    CheckerMode,
    CheckerSourceSupport,
    CheckerSemanticFit,
    CheckerVerdict,
    NormalizationPrecision,
    NormalizedValueType,
    NormalizerDraft,
    NormalizerFieldStatus,
    NormalizerMode,
    NormalizerStrategySource,
    NormalizerValueDraft,
    SourceType,
    TokenUsage,
)
from datacollector.tests import test_checker as checker_fixtures


CHECK_SHA256 = "d" * 64
CHECK_REFERENCE = "/fixtures/check-r004.json"


class FixtureNormalizerLLM:
    model_name = "fake-normalizer-model"

    def __init__(self, draft_factory=None):
        self.draft_factory = draft_factory
        self.calls = []

    def generate(
        self,
        plan,
        search_results,
        extraction_results,
        checker_results,
        claim_ids,
        system_prompt,
        *,
        iteration,
        call_index,
    ):
        self.calls.append(claim_ids)
        claim_by_id = {claim.claim_id: claim for claim in extraction_results.claims}
        if self.draft_factory is not None:
            draft = self.draft_factory(claim_ids, claim_by_id)
        else:
            grouped = defaultdict(list)
            for claim_id in claim_ids:
                claim = claim_by_id[claim_id]
                grouped[(claim.task_id, claim.target_field, claim.value_text)].append(
                    claim_id
                )
            draft = NormalizerDraft(
                values=[
                    NormalizerValueDraft(
                        task_id=task_id,
                        target_field=target_field,
                        claim_ids=group_claim_ids,
                        value_type=NormalizedValueType.TEXT,
                        canonical_text=value_text,
                        precision=NormalizationPrecision.EXACT,
                    )
                    for (
                        task_id,
                        target_field,
                        value_text,
                    ), group_claim_ids in grouped.items()
                ]
            )
        citation_by_id = {
            citation.citation_id: citation
            for citation in extraction_results.citations
        }
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
        return NormalizerGeneration(
            draft=draft,
            usage=AgentIterationUsage(
                agent="normalizer",
                iteration=iteration,
                call_index=call_index,
                scope_task_ids=scope_task_ids,
                scope_source_ids=scope_source_ids,
                requested_model=self.model_name,
                resolved_model=self.model_name,
                tokens=TokenUsage(
                    input_tokens=500,
                    output_tokens=100,
                    total_tokens=600,
                ),
            ),
        )


class FailingNormalizerLLM(FixtureNormalizerLLM):
    def generate(self, *args, **kwargs):
        extraction_results = args[2]
        claim_ids = args[4]
        claim_by_id = {claim.claim_id: claim for claim in extraction_results.claims}
        citation_by_id = {
            citation.citation_id: citation
            for citation in extraction_results.citations
        }
        raise NormalizerProviderError(
            "Fixture provider failure.",
            code="provider_exception",
            iteration=kwargs["iteration"],
            call_index=kwargs["call_index"],
            scope_task_ids=list(
                dict.fromkeys(claim_by_id[claim_id].task_id for claim_id in claim_ids)
            ),
            scope_source_ids=list(
                dict.fromkeys(
                    citation_by_id[citation_id].source_id
                    for claim_id in claim_ids
                    for citation_id in claim_by_id[claim_id].citation_ids
                )
            ),
            requested_model=self.model_name,
        )


class NormalizerAgentTests(TestCase):
    @classmethod
    def setUpClass(cls):
        checker_fixtures.CheckerAgentTests.setUpClass()
        cls.plan = checker_fixtures.CheckerAgentTests.plan
        cls.search_results = checker_fixtures.CheckerAgentTests.search_results
        cls.extraction_results = checker_fixtures.CheckerAgentTests.extraction_results
        cls.checker_results = CheckerAgent(
            checker_fixtures.FixtureCheckerLLM(
                checker_fixtures.CheckerAgentTests._accepted_draft
            )
        ).create_check_results(
            cls.plan,
            cls.search_results,
            cls.extraction_results,
            plan_sha256=checker_fixtures.PLAN_SHA256,
            search_sha256=checker_fixtures.SEARCH_SHA256,
            extraction_sha256=checker_fixtures.EXTRACTION_SHA256,
            extraction_reference=checker_fixtures.EXTRACTION_REFERENCE,
            plan_reference=checker_fixtures.PLAN_REFERENCE,
            search_reference=checker_fixtures.SEARCH_REFERENCE,
            iteration=4,
        )

    def _run(self, llm=None, *, checker_results=None, mode=None, **kwargs):
        return NormalizerAgent(llm).create_normalizer_results(
            self.plan,
            self.search_results,
            self.extraction_results,
            checker_results or self.checker_results,
            plan_sha256=kwargs.pop("plan_sha256", checker_fixtures.PLAN_SHA256),
            search_sha256=kwargs.pop("search_sha256", checker_fixtures.SEARCH_SHA256),
            extraction_sha256=kwargs.pop(
                "extraction_sha256", checker_fixtures.EXTRACTION_SHA256
            ),
            check_sha256=kwargs.pop("check_sha256", CHECK_SHA256),
            check_reference=CHECK_REFERENCE,
            plan_reference=checker_fixtures.PLAN_REFERENCE,
            search_reference=checker_fixtures.SEARCH_REFERENCE,
            extraction_reference=checker_fixtures.EXTRACTION_REFERENCE,
            iteration=kwargs.pop("iteration", 4),
            mode=mode,
            **kwargs,
        )

    def test_free_normalizer_preserves_every_accepted_claim_and_provenance(self):
        results = self._run(mode=NormalizerMode.FREE)

        self.assertEqual(results.strategy_source, NormalizerStrategySource.DETERMINISTIC)
        self.assertFalse(results.provider_executed)
        self.assertEqual(results.agent_usage, [])
        self.assertEqual(
            len(results.eligible_claim_ids), len(self.extraction_results.claims)
        )
        self.assertEqual(len(results.normalized_values), len(results.eligible_claim_ids))
        self.assertTrue(
            all(value.citation_ids and value.source_ids for value in results.normalized_values)
        )
        self.assertFalse(results.publishable)
        self.assertTrue(results.ready_for_human_review)

    def test_risk_based_low_risk_scope_is_source_typed_and_bounded(self):
        claims = [
            SimpleNamespace(
                claim_id=f"claim-{index:016x}",
                task_id="task-training",
                target_field="support.training_program",
                value_text=f"Szkolenie {index}",
                citation_ids=["citation-training"],
            )
            for index in range(4)
        ]
        plan = SimpleNamespace(
            profile_snapshot=SimpleNamespace(profile_id="PL:L1:v2"),
            planner_input=SimpleNamespace(profile_id="PL:L1:v2"),
        )
        search = SimpleNamespace(
            sources=[
                SimpleNamespace(
                    source_id="source-official",
                    source_type=SourceType.OFFICIAL,
                )
            ]
        )
        extraction = SimpleNamespace(
            claims=claims,
            citations=[
                SimpleNamespace(
                    citation_id="citation-training",
                    source_id="source-official",
                )
            ],
        )
        checker = SimpleNamespace(
            checker_mode=CheckerMode.RISK_BASED,
            claim_decisions=[
                SimpleNamespace(
                    claim_id=claim.claim_id,
                    verdict=CheckerVerdict.NOT_REVIEWED,
                    semantic_fit=CheckerSemanticFit.NOT_REVIEWED,
                    source_support=CheckerSourceSupport.NOT_REVIEWED,
                )
                for claim in claims
            ],
        )

        selected = _bounded_low_risk_claim_ids(
            plan,
            search,
            extraction,
            checker,
            unsafe_claim_ids=set(),
        )

        self.assertEqual(
            selected,
            [claim.claim_id for claim in claims[:3]],
        )

        search.sources[0].source_type = SourceType.UNKNOWN
        self.assertEqual(
            _bounded_low_risk_claim_ids(
                plan,
                search,
                extraction,
                checker,
                unsafe_claim_ids=set(),
            ),
            [],
        )

    def test_compatible_low_risk_descriptions_are_compacted_to_one_value(self):
        claims = [
            SimpleNamespace(
                claim_id=f"claim-{index:016x}",
                task_id="task-training",
                target_field="support.training_program",
                value_text=value,
                citation_ids=[f"citation-{index}"],
            )
            for index, value in enumerate(
                [
                    "Szkolenie przed otwarciem.",
                    "Wsparcie w pozyskiwaniu klientów.",
                ],
                start=1,
            )
        ]
        draft = NormalizerDraft(
            values=[
                NormalizerValueDraft(
                    task_id=claim.task_id,
                    target_field=claim.target_field,
                    claim_ids=[claim.claim_id],
                    value_type=NormalizedValueType.TEXT,
                    canonical_text=claim.value_text,
                    precision=NormalizationPrecision.QUALITATIVE,
                )
                for claim in claims
            ]
        )

        values = NormalizerAgent._materialize_values(
            draft,
            {claim.claim_id: claim for claim in claims},
            {
                claim.claim_id: SimpleNamespace(
                    source_support=CheckerSourceSupport.NOT_REVIEWED
                )
                for claim in claims
            },
            {
                f"citation-{index}": SimpleNamespace(source_id=f"source-{index}")
                for index in range(1, 3)
            },
            low_risk_claim_ids={claim.claim_id for claim in claims},
        )

        self.assertEqual(len(values), 1)
        self.assertEqual(values[0].claim_ids, [claim.claim_id for claim in claims])
        self.assertEqual(
            values[0].canonical_text,
            "Szkolenie przed otwarciem; Wsparcie w pozyskiwaniu klientów",
        )
        self.assertFalse(values[0].needs_corroboration)

        field_results, ignored = NormalizerAgent._build_field_results(
            SimpleNamespace(
                contradictions=[],
                task_results=[
                    SimpleNamespace(
                        task_id="task-training",
                        field_results=[
                            SimpleNamespace(
                                target_field="support.training_program",
                                status=CheckerFieldStatus.MISSING,
                                audit_basis=None,
                                accepted_claim_ids=[],
                                rejected_claim_ids=[],
                                needs_review_claim_ids=[],
                            )
                        ],
                    )
                ],
            ),
            values,
            eligible_claim_ids=[claim.claim_id for claim in claims],
            unsafe_excluded_claim_ids=[],
            low_risk_claim_ids=[claim.claim_id for claim in claims],
        )

        self.assertEqual(ignored, 0)
        self.assertEqual(
            field_results[0].checker_status,
            CheckerFieldStatus.NOT_REVIEWED,
        )

    def test_local_quality_audit_becomes_derived_field_without_a_fake_value(self):
        task_result = self.checker_results.task_results[0]
        audit_field = task_result.field_results[0].model_copy(
            update={
                "target_field": "quality.no_guessing_rule",
                "status": CheckerFieldStatus.VERIFIED,
                "raw_claim_ids": [],
                "accepted_claim_ids": [],
                "rejected_claim_ids": [],
                "needs_review_claim_ids": [],
                "source_ids": [],
                "issue_codes": [],
                "audit_basis": (
                    "Local compliance rules require unresolved facts to remain "
                    "explicit instead of guessed."
                ),
            }
        )
        checker = self.checker_results.model_copy(
            update={
                "task_results": [
                    task_result.model_copy(update={"field_results": [audit_field]})
                ],
                "contradictions": [],
            }
        )

        fields, ignored = NormalizerAgent._build_field_results(
            checker,
            [],
            eligible_claim_ids=[],
            unsafe_excluded_claim_ids=[],
        )

        self.assertEqual(ignored, 0)
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0].status, NormalizerFieldStatus.DERIVED)
        self.assertEqual(fields[0].normalized_value_ids, [])
        self.assertEqual(fields[0].source_ids, [])
        self.assertTrue(fields[0].notes)

    def test_normalizer_rejects_incremental_checker_even_with_accepted_claims(self):
        incremental = self.checker_results.model_copy(
            update={"checker_mode": CheckerMode.INCREMENTAL}
        )

        with self.assertRaisesRegex(NormalizerValidationError, "full Checker"):
            self._run(
                FixtureNormalizerLLM(),
                checker_results=incremental,
                mode=NormalizerMode.PAID,
            )

    def test_typed_money_draft_uses_decimal_strings(self):
        value = NormalizerValueDraft(
            task_id="task-money",
            target_field="fees.initial",
            claim_ids=["claim-aaaaaaaaaaaaaaaa"],
            value_type=NormalizedValueType.MONEY,
            canonical_text="100 000–150 000 PLN",
            number_min="100000",
            number_max="150000",
            currency="PLN",
            precision=NormalizationPrecision.RANGE,
        )

        value.validate_semantics()
        self.assertEqual(value.number_min, "100000")
        self.assertEqual(value.currency, "PLN")

    def test_typed_date_draft_rejects_impossible_calendar_date(self):
        value = NormalizerValueDraft(
            task_id="task-date",
            target_field="documents.issue_dates",
            claim_ids=["claim-aaaaaaaaaaaaaaaa"],
            value_type=NormalizedValueType.DATE,
            canonical_text="2026-02-31",
            date_value="2026-02-31",
            precision=NormalizationPrecision.EXACT,
        )

        with self.assertRaises(ValueError):
            value.validate_semantics()

    def test_paid_normalizer_groups_equivalent_claims_and_records_usage(self):
        results = self._run(FixtureNormalizerLLM(), mode=NormalizerMode.PAID)

        self.assertEqual(results.schema_version, "1.2.0")
        self.assertEqual(results.prompt_version, "normalizer-system-v2")
        self.assertEqual(results.strategy_source, NormalizerStrategySource.OPENAI)
        self.assertEqual(len(results.agent_usage), 1)
        self.assertEqual(
            results.repair_summary.accepted_provider_value_groups,
            results.repair_summary.provider_value_groups,
        )
        self.assertEqual(results.repair_summary.repaired_provider_value_groups, 0)
        self.assertEqual(len(results.normalized_values), 7)
        self.assertTrue(
            all(
                field.status == NormalizerFieldStatus.NORMALIZED
                for field in results.field_results
            )
        )

    def test_rejected_claim_is_excluded_from_normalized_values(self):
        def reject_first(extraction_results):
            draft = checker_fixtures.CheckerAgentTests._accepted_draft(
                extraction_results
            )
            payload = draft.decisions[0].model_dump(mode="python")
            payload.update(
                {
                    "verdict": "rejected",
                    "semantic_fit": "mismatch",
                    "issue_codes": ["unsupported_claim"],
                }
            )
            draft.decisions[0] = CheckerClaimDecisionDraft.model_validate(payload)
            return draft

        changed = CheckerAgent(
            checker_fixtures.FixtureCheckerLLM(reject_first)
        ).create_check_results(
            self.plan,
            self.search_results,
            self.extraction_results,
            plan_sha256=checker_fixtures.PLAN_SHA256,
            search_sha256=checker_fixtures.SEARCH_SHA256,
            extraction_sha256=checker_fixtures.EXTRACTION_SHA256,
            extraction_reference=checker_fixtures.EXTRACTION_REFERENCE,
            plan_reference=checker_fixtures.PLAN_REFERENCE,
            search_reference=checker_fixtures.SEARCH_REFERENCE,
            iteration=4,
        )

        results = self._run(
            checker_results=changed,
            mode=NormalizerMode.FREE,
            allow_incomplete=True,
        )

        self.assertIn(changed.selected_claim_ids[0], results.excluded_claim_ids)
        self.assertNotIn(changed.selected_claim_ids[0], results.eligible_claim_ids)
        self.assertFalse(
            any(
                changed.selected_claim_ids[0] in value.claim_ids
                for value in results.normalized_values
            )
        )

    def test_invalid_paid_claim_coverage_falls_back_with_usage(self):
        def invalid_draft(claim_ids, claim_by_id):
            claim = claim_by_id[claim_ids[0]]
            return NormalizerDraft(
                values=[
                    NormalizerValueDraft(
                        task_id=claim.task_id,
                        target_field=claim.target_field,
                        claim_ids=[claim.claim_id],
                        value_type=NormalizedValueType.TEXT,
                        canonical_text=claim.value_text,
                        precision=NormalizationPrecision.EXACT,
                    )
                ]
            )

        results = self._run(
            FixtureNormalizerLLM(invalid_draft),
            mode=NormalizerMode.PAID,
        )

        self.assertEqual(
            results.strategy_source,
            NormalizerStrategySource.DETERMINISTIC_FALLBACK,
        )
        self.assertEqual(len(results.agent_usage), 1)
        self.assertEqual(results.failed_attempts[0].error_code, "invalid_claim_coverage")
        self.assertEqual(len(results.normalized_values), len(results.eligible_claim_ids))

    def test_invalid_paid_typed_value_repairs_only_bad_group_and_preserves_usage(self):
        def invalid_draft(claim_ids, claim_by_id):
            values = []
            for index, claim_id in enumerate(claim_ids):
                claim = claim_by_id[claim_id]
                values.append(
                    NormalizerValueDraft(
                        task_id=claim.task_id,
                        target_field=claim.target_field,
                        claim_ids=[claim_id],
                        value_type=(
                            NormalizedValueType.DATE
                            if index == 0
                            else NormalizedValueType.TEXT
                        ),
                        canonical_text=claim.value_text,
                        date_value="2026-02-31" if index == 0 else None,
                        precision=NormalizationPrecision.EXACT,
                        notes="provider-kept" if index else "provider-invalid",
                    )
                )
            return NormalizerDraft(values=values)

        results = self._run(
            FixtureNormalizerLLM(invalid_draft),
            mode=NormalizerMode.PAID,
        )

        self.assertEqual(
            results.strategy_source,
            NormalizerStrategySource.OPENAI_REPAIRED,
        )
        self.assertEqual(len(results.agent_usage), 1)
        self.assertEqual(results.failed_attempts, [])
        self.assertEqual(results.repair_summary.repaired_provider_value_groups, 1)
        self.assertEqual(results.repair_summary.deterministic_replacement_values, 1)
        self.assertEqual(results.repair_summary.issue_codes, ["invalid_date_value"])
        self.assertEqual(
            sum(value.notes == "provider-kept" for value in results.normalized_values),
            len(results.eligible_claim_ids) - 1,
        )
        repaired = next(
            value
            for value in results.normalized_values
            if value.claim_ids == [results.eligible_claim_ids[0]]
        )
        self.assertEqual(repaired.value_type, NormalizedValueType.TEXT)
        self.assertEqual(
            repaired.notes,
            "Conservative deterministic text normalization.",
        )

    def test_provider_failure_retains_zero_invention_fallback(self):
        results = self._run(FailingNormalizerLLM(), mode=NormalizerMode.PAID)

        self.assertEqual(
            results.strategy_source,
            NormalizerStrategySource.DETERMINISTIC_FALLBACK,
        )
        self.assertTrue(results.failed_attempts[0].token_usage_unknown)
        self.assertEqual(results.agent_usage, [])

    def test_ineligible_checker_contradiction_is_ignored(self):
        decisions = self.checker_results.claim_decisions
        first = decisions[0]
        second = decisions[7]
        rejected_second = second.model_copy(
            update={
                "verdict": CheckerVerdict.REJECTED,
                "semantic_fit": "mismatch",
                "source_support": "unsuitable",
                "issue_codes": ["unsupported_claim"],
            }
        )
        field = self.checker_results.task_results[0].field_results[0]
        changed_field = field.model_copy(
            update={
                "accepted_claim_ids": [first.claim_id],
                "rejected_claim_ids": [second.claim_id],
                "status": "partial",
            }
        )
        changed_task = self.checker_results.task_results[0].model_copy(
            update={
                "field_results": [
                    changed_field,
                    *self.checker_results.task_results[0].field_results[1:],
                ],
                "status": "partial",
            }
        )
        contradiction = CheckerContradiction(
            contradiction_id="contradiction-aaaaaaaaaaaaaaaa",
            task_id=first.task_id,
            target_field=first.target_field,
            claim_ids=[first.claim_id, second.claim_id],
            kind=CheckerContradictionKind.TEMPORAL_MISMATCH,
            rationale="Fixture contradiction includes a rejected claim.",
        )
        changed = self.checker_results.model_copy(
            update={
                "claim_decisions": [
                    *decisions[:7],
                    rejected_second,
                    *decisions[8:],
                ],
                "contradictions": [contradiction],
                "task_results": [changed_task],
                "passed": False,
                "selected_scope_ready": False,
                "recommended_next_action": "resolve_gaps",
            }
        )

        results = self._run(
            checker_results=changed,
            mode=NormalizerMode.FREE,
            allow_incomplete=True,
        )

        brand_field = results.field_results[0]
        self.assertNotEqual(brand_field.status, NormalizerFieldStatus.CONFLICTING)
        self.assertTrue(any("Ignored 1 Checker contradiction" in item for item in results.warnings))

    def test_rejects_broken_checker_lineage(self):
        with self.assertRaisesRegex(
            NormalizerValidationError,
            "exact artifact bytes",
        ):
            self._run(extraction_sha256="e" * 64)

    def test_failed_checker_requires_explicit_incomplete_override(self):
        changed = self.checker_results.model_copy(
            update={"passed": False, "selected_scope_ready": False}
        )

        with self.assertRaisesRegex(
            NormalizerValidationError,
            "--allow-incomplete",
        ):
            self._run(checker_results=changed, mode=NormalizerMode.FREE)

    def test_free_cli_writes_normalized_summary(self):
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            plan_path = (directory / "plan.json").resolve()
            search_path = (directory / "sources-r004.json").resolve()
            extraction_path = (directory / "extractions-r004.json").resolve()
            check_path = (directory / "check-r004.json").resolve()
            checker = self.checker_results.model_copy(
                update={
                    "plan_reference": str(plan_path),
                    "search_reference": str(search_path),
                    "extraction_reference": str(extraction_path),
                }
            )
            expected_path = directory / "normalized-r004-free.json"
            output = StringIO()
            with (
                patch(
                    "datacollector.cli.load_checker_results",
                    return_value=(checker, CHECK_SHA256),
                ),
                patch(
                    "datacollector.cli.load_extraction_results",
                    return_value=(
                        self.extraction_results,
                        checker_fixtures.EXTRACTION_SHA256,
                    ),
                ),
                patch(
                    "datacollector.cli.load_search_results",
                    return_value=(
                        self.search_results,
                        checker_fixtures.SEARCH_SHA256,
                    ),
                ),
                patch(
                    "datacollector.cli.load_research_plan",
                    return_value=(self.plan, checker_fixtures.PLAN_SHA256),
                ),
                patch(
                    "datacollector.cli.save_normalizer_results",
                    return_value=expected_path,
                ),
                redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "normalize",
                        "--check",
                        str(check_path),
                        "--plan",
                        str(plan_path),
                        "--sources",
                        str(search_path),
                        "--extractions",
                        str(extraction_path),
                        "--free",
                        "--iteration",
                        "4",
                    ]
                )

            summary = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertFalse(summary["publishable"])
            self.assertEqual(summary["usage_totals"]["total_tokens"], 0)
            self.assertEqual(summary["repair_summary"]["provider_value_groups"], 0)
            self.assertEqual(summary["normalized_path"], str(expected_path))

    def test_cli_blocks_incomplete_checker_before_openai_configuration(self):
        changed = self.checker_results.model_copy(
            update={"passed": False, "selected_scope_ready": False}
        )
        error = StringIO()
        with (
            patch(
                "datacollector.cli.load_checker_results",
                return_value=(changed, CHECK_SHA256),
            ),
            patch("datacollector.cli.OpenAISettings.from_env") as settings,
            redirect_stderr(error),
        ):
            exit_code = main(
                ["normalize", "--check", "/fixtures/check-r004.json"]
            )

        self.assertEqual(exit_code, 2)
        settings.assert_not_called()
        self.assertIn("--allow-incomplete", error.getvalue())
