import json
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from datacollector.agents.planner import PlannerAgent
from datacollector.catalog import load_question_catalog
from datacollector.config import OpenAISettings
from datacollector.llm.openai_checker_client import OpenAICheckerClient
from datacollector.llm.protocol import CheckerProviderError
from datacollector.schemas import (
    CheckerClaimDecisionDraft,
    CheckerDraft,
    CheckerModelSemanticFit,
    CheckerModelSourceSupport,
    CheckerModelVerdict,
    ExtractionCitation,
    ExtractionConfidence,
    ExtractionTaskStatus,
    FieldExtractionStatus,
    PlannerInput,
    RawExtractionClaim,
    SearchQueryCoverage,
    SearchSource,
    SearchSourceOrigin,
    SearchTaskStatus,
    SourceType,
)


NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
EXACT_QUOTE = "Example Polska sp. z o.o. is the official franchise operator."


class FakeResponses:
    def __init__(
        self,
        draft,
        *,
        error=None,
        response_status="completed",
        invalid_usage=False,
        refusal=None,
    ):
        self.draft = draft
        self.error = error
        self.response_status = response_status
        self.invalid_usage = invalid_usage
        self.refusal = refusal
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        usage = None
        if not self.invalid_usage:
            usage = SimpleNamespace(
                input_tokens=1000,
                input_tokens_details=SimpleNamespace(
                    cached_tokens=0,
                    cache_write_tokens=0,
                ),
                output_tokens=100,
                output_tokens_details=SimpleNamespace(reasoning_tokens=20),
                total_tokens=1100,
            )
        output = []
        if self.refusal is not None:
            output = [
                SimpleNamespace(
                    content=[
                        SimpleNamespace(type="refusal", refusal=self.refusal)
                    ]
                )
            ]
        return SimpleNamespace(
            id="resp_check",
            _request_id="req_check",
            model="gpt-5.6-terra",
            service_tier="default",
            status=self.response_status,
            output=output,
            output_parsed=self.draft,
            usage=usage,
        )


class FakeOpenAI:
    def __init__(self, draft, **kwargs):
        self.responses = FakeResponses(draft, **kwargs)


class OpenAICheckerClientTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = PlannerAgent(load_question_catalog()).create_plan(
            PlannerInput(
                brand_name="Example",
                target_country="PL",
                depth="catalog",
            )
        )
        cls.task = cls.plan.tasks[0]
        cls.source = SearchSource(
            source_id="source-aaaaaaaaaaaaaaaa",
            url="https://example.com/franchise",
            canonical_url="https://example.com/franchise",
            title="Official franchise information",
            source_type=SourceType.OFFICIAL,
            origin=SearchSourceOrigin.OPENAI_WEB_SEARCH,
            provider_observed=True,
            task_ids=[cls.task.task_id],
            observed_in_action_ids=["action-check-fixture"],
            discovered_via_queries=[],
            relevance_note="Official source for the mapped identity task.",
            discovered_at=NOW,
        )
        cls.citation = ExtractionCitation(
            citation_id="citation-bbbbbbbbbbbbbbbb",
            passage_id="passage-cccccccccccccccc",
            document_id="document-dddddddddddddddd",
            source_id=cls.source.source_id,
            text_sha256="a" * 64,
            quote=EXACT_QUOTE,
            start_char=0,
            end_char=len(EXACT_QUOTE),
            locator=f"chars:0-{len(EXACT_QUOTE)}",
        )
        cls.claim = RawExtractionClaim(
            claim_id="claim-eeeeeeeeeeeeeeee",
            task_id=cls.task.task_id,
            target_field=cls.task.target_fields[0],
            value_text="Example Polska sp. z o.o.",
            citation_ids=[cls.citation.citation_id],
            publisher_text="Example",
            publication_date_text="2026-07-01",
            confidence=ExtractionConfidence.HIGH,
        )
        cls.search_results = SimpleNamespace(
            search_id=str(uuid4()),
            task_results=[
                SimpleNamespace(
                    task_id=cls.task.task_id,
                    status=SearchTaskStatus.SOURCES_FOUND,
                    query_coverage=SearchQueryCoverage.COMPLETE,
                    minimum_sources=1,
                    source_ids=[cls.source.source_id],
                    coverage_gaps=[],
                    unresolved_targets=[],
                )
            ],
        )
        cls.extraction_results = SimpleNamespace(
            extraction_id=str(uuid4()),
            created_at=NOW,
            claims=[cls.claim],
            citations=[cls.citation],
            task_results=[
                SimpleNamespace(
                    task_id=cls.task.task_id,
                    status=ExtractionTaskStatus.COMPLETE,
                    source_ids=[cls.source.source_id],
                    field_results=[
                        SimpleNamespace(
                            target_field=cls.task.target_fields[0],
                            status=FieldExtractionStatus.EXTRACTED,
                            claim_ids=[cls.claim.claim_id],
                            source_ids_considered=[cls.source.source_id],
                        )
                    ],
                    unresolved_targets=[],
                    inherited_search_unresolved_targets=[],
                    coverage_gaps=[],
                )
            ],
        )

    def _draft(self):
        return CheckerDraft(
            decisions=[
                CheckerClaimDecisionDraft(
                    claim_id=self.claim.claim_id,
                    verdict=CheckerModelVerdict.ACCEPTED,
                    semantic_fit=CheckerModelSemanticFit.DIRECT,
                    source_support=CheckerModelSourceSupport.SUFFICIENT,
                    rationale="The exact quote directly identifies the operator.",
                )
            ]
        )

    def _generate(self, fake_client, *, iteration=2, call_index=1):
        client = OpenAICheckerClient(
            OpenAISettings(api_key="test", model="gpt-5.6-terra"),
            client=fake_client,
        )
        return client.generate(
            self.plan,
            self.search_results,
            self.extraction_results,
            [self.task],
            [self.source],
            "Checker system prompt",
            iteration=iteration,
            call_index=call_index,
        )

    @patch("datacollector.llm.openai_checker_client.OpenAI")
    def test_constructor_disables_hidden_sdk_retries(self, openai):
        settings = OpenAISettings(
            api_key="test",
            model="gpt-5.6-terra",
            timeout_seconds=37,
            max_retries=9,
        )

        OpenAICheckerClient(settings)

        openai.assert_called_once_with(
            api_key="test",
            timeout=37,
            max_retries=0,
        )

    def test_request_contains_minimal_claim_review_payload_and_exact_quote(self):
        draft = self._draft()
        fake_client = FakeOpenAI(draft)

        generation = self._generate(fake_client)

        request = fake_client.responses.kwargs
        self.assertEqual(request["model"], "gpt-5.6-terra")
        self.assertEqual(request["reasoning"], {"effort": "medium"})
        self.assertEqual(request["max_output_tokens"], 8000)
        self.assertFalse(request["store"])
        self.assertIs(request["text_format"], CheckerDraft)
        self.assertNotIn("tools", request)
        self.assertNotIn("tool_choice", request)
        self.assertNotIn("max_tool_calls", request)
        self.assertNotIn("prompt_cache_options", request)
        self.assertEqual(
            request["metadata"],
            {
                "agent": "checker",
                "iteration": "2",
                "call_index": "1",
                "plan_run_id": self.plan.run_id,
                "extraction_id": self.extraction_results.extraction_id,
            },
        )
        self.assertEqual(
            request["input"][0],
            {"role": "system", "content": "Checker system prompt"},
        )
        payload = json.loads(request["input"][1]["content"])
        self.assertRegex(
            payload["checker_context"]["current_date"],
            r"^\d{4}-\d{2}-\d{2}$",
        )
        self.assertEqual(
            [task["task_id"] for task in payload["tasks"]],
            [self.task.task_id],
        )
        self.assertEqual(
            [source["source_id"] for source in payload["sources"]],
            [self.source.source_id],
        )
        self.assertEqual(
            payload["claims"][0]["citations"],
            [
                {
                    "citation_id": self.citation.citation_id,
                    "source_id": self.source.source_id,
                    "quote": EXACT_QUOTE,
                    "locator": self.citation.locator,
                }
            ],
        )
        self.assertEqual(
            payload["required_output"]["decision_claim_ids"],
            [self.claim.claim_id],
        )
        self.assertEqual(
            payload["task_coverage"][0]["extraction"]["field_results"][0][
                "claim_ids"
            ],
            [self.claim.claim_id],
        )
        self.assertNotIn("documents", payload)
        self.assertNotIn("evidence_passages", payload)
        self.assertNotIn("passages", payload)
        self.assertIs(generation.draft, draft)
        self.assertEqual(generation.usage.agent, "checker")
        self.assertEqual(generation.usage.iteration, 2)
        self.assertEqual(generation.usage.call_index, 1)
        self.assertEqual(generation.usage.scope_task_ids, [self.task.task_id])
        self.assertEqual(
            generation.usage.scope_source_ids,
            [self.source.source_id],
        )
        self.assertEqual(generation.usage.tool_usage, [])
        self.assertEqual(
            generation.usage.cost_estimate.total_estimated_cost_usd,
            Decimal("0.00400000"),
        )

    def test_incomplete_response_preserves_usage_and_attempt_context(self):
        fake_client = FakeOpenAI(self._draft(), response_status="incomplete")

        with self.assertRaises(CheckerProviderError) as raised:
            self._generate(fake_client, iteration=4)

        error = raised.exception
        self.assertEqual(error.code, "incomplete_response")
        self.assertIsNotNone(error.usage)
        self.assertEqual(error.usage.iteration, 4)
        self.assertEqual(error.scope_task_ids, [self.task.task_id])
        self.assertEqual(error.scope_source_ids, [self.source.source_id])

    def test_refusal_preserves_usage_without_exposing_refusal_text(self):
        secret_refusal = "refusal contains private fixture text"
        fake_client = FakeOpenAI(None, refusal=secret_refusal)

        with self.assertRaises(CheckerProviderError) as raised:
            self._generate(fake_client)

        error = raised.exception
        self.assertEqual(error.code, "refusal")
        self.assertIsNotNone(error.usage)
        self.assertNotIn(secret_refusal, str(error))

    def test_missing_structured_output_preserves_usage(self):
        fake_client = FakeOpenAI(None)

        with self.assertRaises(CheckerProviderError) as raised:
            self._generate(fake_client)

        error = raised.exception
        self.assertEqual(error.code, "missing_structured_output")
        self.assertIsNotNone(error.usage)
        self.assertEqual(error.usage.tokens.total_tokens, 1100)

    def test_invalid_usage_preserves_attempt_metadata(self):
        fake_client = FakeOpenAI(self._draft(), invalid_usage=True)

        with self.assertRaises(CheckerProviderError) as raised:
            self._generate(fake_client, iteration=7)

        error = raised.exception
        self.assertEqual(error.code, "invalid_usage")
        self.assertIsNone(error.usage)
        self.assertEqual(error.agent, "checker")
        self.assertEqual(error.iteration, 7)
        self.assertEqual(error.call_index, 1)
        self.assertEqual(error.requested_model, "gpt-5.6-terra")
        self.assertEqual(error.scope_task_ids, [self.task.task_id])
        self.assertEqual(error.scope_source_ids, [self.source.source_id])

    def test_transport_error_does_not_expose_provider_message_or_api_key(self):
        secret = "sk-test-do-not-leak"
        fake_client = FakeOpenAI(
            self._draft(),
            error=RuntimeError(f"transport failed for {secret}"),
        )
        client = OpenAICheckerClient(
            OpenAISettings(api_key=secret, model="gpt-5.6-terra"),
            client=fake_client,
        )

        with self.assertRaises(CheckerProviderError) as raised:
            client.generate(
                self.plan,
                self.search_results,
                self.extraction_results,
                [self.task],
                [self.source],
                "Checker system prompt",
                iteration=1,
                call_index=1,
            )

        error = raised.exception
        self.assertEqual(error.code, "provider_exception")
        self.assertIn("RuntimeError", str(error))
        self.assertNotIn(secret, str(error))
        rendered_traceback = "".join(traceback.format_exception(error))
        self.assertNotIn(secret, rendered_traceback)
