import hashlib
from datetime import datetime, timezone
from unittest import TestCase
from uuid import uuid4

from pydantic import ValidationError

from datacollector.agents.planner import PlannerAgent
from datacollector.catalog import load_question_catalog
from datacollector.schemas import (
    AgentIterationUsage,
    DocumentParseStatus,
    DocumentRetrievalStatus,
    EvidencePassage,
    ExtractionCitation,
    ExtractionConfidence,
    ExtractionLimits,
    ExtractionResults,
    ExtractionTaskResult,
    ExtractionTaskStatus,
    FieldExtractionResult,
    FieldExtractionStatus,
    PlannerInput,
    RawExtractionClaim,
    ResearchPlan,
    SourceDocument,
    TokenUsage,
)


class PlannerInputTests(TestCase):
    def test_country_code_is_trimmed_and_normalized_before_validation(self):
        planner_input = PlannerInput(brand_name="Example", target_country=" us ")

        self.assertEqual(planner_input.target_country, "US")

    def test_country_code_must_be_two_ascii_letters(self):
        for invalid_code in ("1!", "USA", "PŁ", ""):
            with self.subTest(code=invalid_code):
                with self.assertRaises(ValidationError):
                    PlannerInput(brand_name="Example", target_country=invalid_code)


class ResearchPlanContractTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = PlannerAgent(load_question_catalog()).create_plan(
            PlannerInput(brand_name="Example")
        )

    def validate_mutated_plan(self, mutate):
        payload = self.plan.model_dump(mode="json")
        mutate(payload)
        return ResearchPlan.model_validate(payload)

    def test_run_id_must_be_uuid4(self):
        with self.assertRaisesRegex(ValidationError, "UUIDv4"):
            self.validate_mutated_plan(lambda payload: payload.update(run_id="not-a-uuid"))

    def test_offline_plan_cannot_declare_model(self):
        with self.assertRaisesRegex(ValidationError, "Offline plans"):
            self.validate_mutated_plan(
                lambda payload: payload.update(model="unexpected-model")
            )

    def test_task_dependencies_must_reference_known_tasks(self):
        def add_unknown_dependency(payload):
            payload["tasks"][0]["depends_on"] = ["task-that-does-not-exist"]

        with self.assertRaisesRegex(ValidationError, "unknown dependencies"):
            self.validate_mutated_plan(add_unknown_dependency)

    def test_critical_fields_must_match_critical_tasks(self):
        with self.assertRaisesRegex(ValidationError, "critical_fields"):
            self.validate_mutated_plan(
                lambda payload: payload.update(critical_fields=[])
            )

    def test_older_schema_plans_remain_readable(self):
        for version in ("1.0.0", "1.1.0"):
            with self.subTest(version=version):
                legacy_payload = self.plan.model_dump(mode="json")
                legacy_payload["schema_version"] = version

                legacy_plan = ResearchPlan.model_validate(legacy_payload)

                self.assertEqual(legacy_plan.schema_version, version)

    def test_multiple_provider_calls_in_one_agent_iteration_use_call_index(self):
        payload = self.plan.model_dump(mode="json")
        payload.update(generated_by="openai", model="test-model")
        base_usage = AgentIterationUsage(
            agent="planner",
            iteration=1,
            requested_model="test-model",
            resolved_model="test-model",
            tokens=TokenUsage(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
            ),
        )
        payload["agent_usage"] = [
            base_usage.model_dump(mode="json"),
            base_usage.model_copy(update={"call_index": 2}).model_dump(mode="json"),
        ]

        plan = ResearchPlan.model_validate(payload)

        self.assertEqual([item.call_index for item in plan.agent_usage], [1, 2])

    def test_duplicate_agent_iteration_call_index_is_rejected(self):
        payload = self.plan.model_dump(mode="json")
        payload.update(generated_by="openai", model="test-model")
        usage = AgentIterationUsage(
            agent="planner",
            iteration=1,
            requested_model="test-model",
            resolved_model="test-model",
            tokens=TokenUsage(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
            ),
        ).model_dump(mode="json")
        payload["agent_usage"] = [usage, usage]

        with self.assertRaisesRegex(ValidationError, "unique"):
            ResearchPlan.model_validate(payload)


class ExtractionResultsContractTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = PlannerAgent(load_question_catalog()).create_plan(
            PlannerInput(brand_name="Example", depth="catalog")
        )
        cls.task = cls.plan.tasks[0]
        cls.source_id = "source-aaaaaaaaaaaaaaaa"
        cls.document_id = "document-bbbbbbbbbbbbbbbb"
        cls.passage_id = "passage-cccccccccccccccc"
        cls.citation_id = "citation-dddddddddddddddd"
        cls.claim_id = "claim-eeeeeeeeeeeeeeee"
        cls.text = (
            "Oficjalny dokument wskazuje, że operatorem marki jest "
            "Example Polska sp. z o.o. na terytorium Polski."
        )
        cls.quote = "Example Polska sp. z o.o."
        cls.quote_start = cls.text.index(cls.quote)
        cls.quote_end = cls.quote_start + len(cls.quote)
        cls.text_sha256 = hashlib.sha256(cls.text.encode("utf-8")).hexdigest()

    def valid_openai_payload(self):
        content = self.text.encode("utf-8")
        document = SourceDocument(
            document_id=self.document_id,
            source_id=self.source_id,
            canonical_url="https://example.com/franchise",
            final_url="https://example.com/franchise",
            task_ids=[self.task.task_id],
            retrieval_status=DocumentRetrievalStatus.FETCHED,
            parse_status=DocumentParseStatus.PARSED,
            collected_at=datetime.now(timezone.utc),
            http_status=200,
            media_type="text/html",
            content_bytes=len(content),
            content_sha256=hashlib.sha256(content).hexdigest(),
            title="Official franchise document",
            text=self.text,
            text_chars=len(self.text),
            processed_chars=len(self.text),
            text_sha256=self.text_sha256,
            parser="test-html",
        )
        passage = EvidencePassage(
            passage_id=self.passage_id,
            document_id=self.document_id,
            source_id=self.source_id,
            task_id=self.task.task_id,
            start_char=0,
            end_char=len(self.text),
            locator="characters 0-end",
            text=self.text,
            matched_terms=["operator"],
        )
        citation = ExtractionCitation(
            citation_id=self.citation_id,
            passage_id=self.passage_id,
            document_id=self.document_id,
            source_id=self.source_id,
            text_sha256=self.text_sha256,
            quote=self.quote,
            start_char=self.quote_start,
            end_char=self.quote_end,
            locator=f"characters {self.quote_start}-{self.quote_end}",
        )
        claim = RawExtractionClaim(
            claim_id=self.claim_id,
            task_id=self.task.task_id,
            target_field=self.task.target_fields[0],
            value_text=self.quote,
            citation_ids=[self.citation_id],
            confidence=ExtractionConfidence.HIGH,
        )
        task_result = ExtractionTaskResult(
            task_id=self.task.task_id,
            catalog_question_id=self.task.catalog_question_id,
            status=ExtractionTaskStatus.COMPLETE,
            source_ids=[self.source_id],
            document_ids=[self.document_id],
            passage_ids=[self.passage_id],
            claim_ids=[self.claim_id],
            field_results=[
                FieldExtractionResult(
                    task_id=self.task.task_id,
                    target_field=self.task.target_fields[0],
                    status=FieldExtractionStatus.EXTRACTED,
                    claim_ids=[self.claim_id],
                    source_ids_considered=[self.source_id],
                )
            ],
        )
        usage = AgentIterationUsage(
            agent="extractor",
            iteration=1,
            call_index=1,
            scope_task_ids=[self.task.task_id],
            scope_source_ids=[self.source_id],
            requested_model="test-model",
            resolved_model="test-model",
            tokens=TokenUsage(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
            ),
        )
        result = ExtractionResults(
            extraction_id=str(uuid4()),
            plan_run_id=self.plan.run_id,
            search_id=str(uuid4()),
            plan_sha256="1" * 64,
            search_sha256="2" * 64,
            plan_reference="/tmp/plan.json",
            search_reference="/tmp/sources.json",
            created_at=datetime.now(timezone.utc),
            iteration=1,
            generated_by="openai",
            model="test-model",
            brand_name="Example",
            target_country="PL",
            depth="catalog",
            network_executed=True,
            provider_executed=True,
            limits=ExtractionLimits(
                source_limit=1,
                requested_source_ids=[self.source_id],
                max_document_bytes=1_000_000,
                max_document_chars=100_000,
                max_passages_per_task=10,
                max_api_calls=5,
            ),
            selected_task_ids=[self.task.task_id],
            selected_source_ids=[self.source_id],
            unselected_source_ids=[],
            documents=[document],
            evidence_passages=[passage],
            citations=[citation],
            claims=[claim],
            task_results=[task_result],
            warnings=[],
            compliance_rules=[],
            agent_usage=[usage],
            failed_attempts=[],
        )
        return result.model_dump(mode="json")

    def test_exact_passage_and_citation_offsets_are_accepted(self):
        result = ExtractionResults.model_validate(self.valid_openai_payload())

        self.assertEqual(result.evidence_passages[0].text, self.text)
        self.assertEqual(result.citations[0].quote, self.quote)
        self.assertEqual(
            result.documents[0].text[
                result.citations[0].start_char : result.citations[0].end_char
            ],
            self.quote,
        )

    def test_passage_with_wrong_offsets_is_rejected(self):
        payload = self.valid_openai_payload()
        payload["evidence_passages"][0]["start_char"] = 1

        with self.assertRaisesRegex(ValidationError, "Evidence passage is not grounded"):
            ExtractionResults.model_validate(payload)

    def test_citation_requires_exact_offsets_quote_and_document_hash(self):
        mutations = {
            "offset": lambda citation: citation.update(
                start_char=citation["start_char"] - 1
            ),
            "quote": lambda citation: citation.update(
                quote="Example Polska sp. z o.o.X"
            ),
            "hash": lambda citation: citation.update(text_sha256="f" * 64),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                payload = self.valid_openai_payload()
                mutate(payload["citations"][0])

                with self.assertRaisesRegex(
                    ValidationError,
                    "Extraction citation is not grounded",
                ):
                    ExtractionResults.model_validate(payload)

    def test_deterministic_result_rejects_claims_and_citations(self):
        payload = self.valid_openai_payload()
        payload.update(
            generated_by="deterministic",
            model=None,
            provider_executed=False,
            agent_usage=[],
        )

        with self.assertRaisesRegex(
            ValidationError,
            "Deterministic Extractor cannot contain provider facts or claims",
        ):
            ExtractionResults.model_validate(payload)

    def test_deterministic_result_rejects_citations_without_claims(self):
        payload = self.valid_openai_payload()
        payload.update(
            generated_by="deterministic",
            model=None,
            provider_executed=False,
            agent_usage=[],
            claims=[],
        )
        payload["task_results"][0].update(
            status="partial",
            claim_ids=[],
        )
        payload["task_results"][0]["field_results"][0].update(
            status="not_found",
            claim_ids=[],
        )

        with self.assertRaisesRegex(
            ValidationError,
            "Deterministic Extractor cannot contain provider facts or claims",
        ):
            ExtractionResults.model_validate(payload)

    def test_openai_provider_flag_must_match_usage_ledger(self):
        mutations = {
            "usage_without_provider": lambda payload: payload.update(
                provider_executed=False
            ),
            "provider_without_usage": lambda payload: payload.update(agent_usage=[]),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                payload = self.valid_openai_payload()
                mutate(payload)

                with self.assertRaisesRegex(
                    ValidationError,
                    "provider_executed must match recorded provider attempts",
                ):
                    ExtractionResults.model_validate(payload)

    def test_complete_task_cannot_hide_an_empty_field_result_set(self):
        payload = self.valid_openai_payload()
        payload["task_results"][0]["field_results"] = []

        with self.assertRaisesRegex(ValidationError, "field_results"):
            ExtractionResults.model_validate(payload)

    def test_logical_provider_calls_cannot_exceed_recorded_cap(self):
        payload = self.valid_openai_payload()
        second_usage = dict(payload["agent_usage"][0])
        second_usage.update(
            call_index=2,
            response_id="response-second",
        )
        payload["agent_usage"].append(second_usage)
        payload["limits"]["max_api_calls"] = 1

        with self.assertRaisesRegex(ValidationError, "exceed max_api_calls"):
            ExtractionResults.model_validate(payload)

    def test_openai_usage_requires_extractor_agent_iteration_and_known_scope(self):
        mutations = {
            "agent": lambda usage: usage.update(agent="searcher"),
            "iteration": lambda usage: usage.update(iteration=2),
            "scope": lambda usage: usage.update(scope_task_ids=["unknown-task"]),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                payload = self.valid_openai_payload()
                mutate(payload["agent_usage"][0])

                with self.assertRaisesRegex(
                    ValidationError,
                    "Extractor usage has inconsistent agent scope",
                ):
                    ExtractionResults.model_validate(payload)

    def test_openai_result_requires_model_name(self):
        payload = self.valid_openai_payload()
        payload["model"] = None

        with self.assertRaisesRegex(
            ValidationError,
            "OpenAI Extractor must declare its model",
        ):
            ExtractionResults.model_validate(payload)
