import hashlib
import json
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from datacollector.agents.planner import PlannerAgent
from datacollector.catalog import load_question_catalog
from datacollector.config import OpenAISettings
from datacollector.llm.openai_extractor_client import (
    OpenAIExtractorClient,
    ExtractorProviderError,
)
from datacollector.schemas import (
    DocumentParseStatus,
    DocumentRetrievalStatus,
    EvidencePassage,
    ExtractionConfidence,
    ExtractorClaimDraft,
    ExtractorDraft,
    PlannerInput,
    SearchSource,
    SearchSourceOrigin,
    SourceDocument,
    SourceType,
)


class FakeResponses:
    def __init__(
        self,
        draft,
        *,
        error=None,
        response_status="completed",
        invalid_usage=False,
    ):
        self.draft = draft
        self.error = error
        self.response_status = response_status
        self.invalid_usage = invalid_usage
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        usage = None if self.invalid_usage else SimpleNamespace(
            input_tokens=1000,
            input_tokens_details=SimpleNamespace(
                cached_tokens=0,
                cache_write_tokens=0,
            ),
            output_tokens=100,
            output_tokens_details=SimpleNamespace(reasoning_tokens=20),
            total_tokens=1100,
        )
        return SimpleNamespace(
            id="resp_extract",
            _request_id="req_extract",
            model="gpt-5.6-terra",
            service_tier="default",
            status=self.response_status,
            output_parsed=self.draft,
            usage=usage,
        )


class FakeOpenAI:
    def __init__(self, draft, **kwargs):
        self.responses = FakeResponses(draft, **kwargs)


class OpenAIExtractorClientTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = PlannerAgent(load_question_catalog()).create_plan(
            PlannerInput(brand_name="Example", target_country="PL", depth="catalog")
        )
        cls.task = cls.plan.tasks[0]
        cls.unmapped_task = cls.plan.tasks[1]
        cls.source = SearchSource(
            source_id="source-aaaaaaaaaaaaaaaa",
            url="https://example.com/franchise",
            canonical_url="https://example.com/franchise",
            title="Official franchise information",
            source_type=SourceType.OFFICIAL,
            origin=SearchSourceOrigin.OPENAI_WEB_SEARCH,
            provider_observed=True,
            task_ids=[cls.task.task_id],
            observed_in_action_ids=["action-example"],
            discovered_via_queries=[],
            relevance_note="Contains identity details for the mapped task.",
            discovered_at=datetime.now(timezone.utc),
        )
        text = (
            "Example Polska sp. z o.o. is the operator named in the official "
            "franchise information. Its registry number is 1234567890."
        )
        raw_content = text.encode("utf-8")
        cls.document = SourceDocument(
            document_id="document-bbbbbbbbbbbbbbbb",
            source_id=cls.source.source_id,
            canonical_url=cls.source.canonical_url,
            final_url=cls.source.canonical_url,
            task_ids=[cls.task.task_id],
            retrieval_status=DocumentRetrievalStatus.FETCHED,
            parse_status=DocumentParseStatus.PARSED,
            collected_at=datetime.now(timezone.utc),
            http_status=200,
            media_type="text/html",
            content_bytes=len(raw_content),
            content_sha256=hashlib.sha256(raw_content).hexdigest(),
            title="Official franchise information",
            text=text,
            text_chars=len(text),
            processed_chars=len(text),
            text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            parser="test-html",
        )
        cls.passage = EvidencePassage(
            passage_id="passage-cccccccccccccccc",
            document_id=cls.document.document_id,
            source_id=cls.source.source_id,
            task_id=cls.task.task_id,
            start_char=0,
            end_char=len(text),
            locator="characters 0-123",
            text=text,
            matched_terms=["operator"],
        )
        cls.unmapped_passage = EvidencePassage(
            passage_id="passage-dddddddddddddddd",
            document_id=cls.document.document_id,
            source_id=cls.source.source_id,
            task_id=cls.unmapped_task.task_id,
            start_char=0,
            end_char=len(text),
            locator="characters 0-123",
            text=text,
            matched_terms=["operator"],
        )

    def _draft(self):
        quote = "Example Polska sp. z o.o. is the operator named"
        return ExtractorDraft(
            claims=[
                ExtractorClaimDraft(
                    task_id=self.task.task_id,
                    target_field=self.task.target_fields[0],
                    passage_id=self.passage.passage_id,
                    value_text="Example Polska sp. z o.o.",
                    evidence_quote=quote,
                    confidence=ExtractionConfidence.HIGH,
                )
            ],
            warnings=[],
        )

    def _generate(self, fake_client, *, iteration=2, call_index=3):
        client = OpenAIExtractorClient(
            OpenAISettings(api_key="test", model="gpt-5.6-terra"),
            client=fake_client,
        )
        return client.generate(
            self.plan,
            self.source,
            self.document,
            [self.task, self.unmapped_task],
            [self.passage, self.unmapped_passage],
            "Extractor system prompt",
            iteration=iteration,
            call_index=call_index,
        )

    @patch("datacollector.llm.openai_extractor_client.OpenAI")
    def test_constructor_disables_hidden_sdk_retries(self, openai):
        settings = OpenAISettings(
            api_key="test",
            model="gpt-5.6-terra",
            timeout_seconds=37,
            max_retries=9,
        )

        OpenAIExtractorClient(settings)

        openai.assert_called_once_with(
            api_key="test",
            timeout=37,
            max_retries=0,
        )

    def test_structured_request_contains_only_source_mapped_work(self):
        draft = self._draft()
        fake_client = FakeOpenAI(draft)

        generation = self._generate(fake_client)

        request = fake_client.responses.kwargs
        self.assertEqual(request["model"], "gpt-5.6-terra")
        self.assertEqual(request["reasoning"], {"effort": "medium"})
        self.assertEqual(request["max_output_tokens"], 8000)
        self.assertFalse(request["store"])
        self.assertIs(request["text_format"], ExtractorDraft)
        self.assertEqual(
            request["prompt_cache_options"],
            {"mode": "explicit"},
        )
        self.assertNotIn("tools", request)
        self.assertNotIn("tool_choice", request)
        self.assertNotIn("max_tool_calls", request)
        self.assertEqual(
            request["metadata"],
            {
                "agent": "extractor",
                "iteration": "2",
                "call_index": "3",
                "plan_run_id": self.plan.run_id,
                "source_id": self.source.source_id,
            },
        )
        self.assertEqual(request["input"][0], {
            "role": "system",
            "content": "Extractor system prompt",
        })
        payload = json.loads(request["input"][1]["content"])
        self.assertRegex(
            payload["extraction_context"]["current_date"],
            r"^\d{4}-\d{2}-\d{2}$",
        )
        self.assertEqual(
            payload["extraction_context"]["source"]["source_id"],
            self.source.source_id,
        )
        self.assertNotIn("text", payload["extraction_context"]["document"])
        self.assertEqual(
            [task["task_id"] for task in payload["tasks"]],
            [self.task.task_id],
        )
        self.assertEqual(
            [passage["passage_id"] for passage in payload["evidence_passages"]],
            [self.passage.passage_id],
        )
        self.assertIs(generation.draft, draft)
        self.assertEqual(generation.source_id, self.source.source_id)
        self.assertEqual(generation.usage.agent, "extractor")
        self.assertEqual(generation.usage.iteration, 2)
        self.assertEqual(generation.usage.call_index, 3)
        self.assertEqual(
            generation.usage.scope_task_ids,
            [self.task.task_id],
        )
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
        fake_client = FakeOpenAI(
            self._draft(),
            response_status="incomplete",
        )

        with self.assertRaises(ExtractorProviderError) as raised:
            self._generate(fake_client, iteration=4, call_index=5)

        error = raised.exception
        self.assertEqual(error.code, "incomplete_response")
        self.assertIsNotNone(error.usage)
        self.assertEqual(error.usage.agent, "extractor")
        self.assertEqual(error.usage.iteration, 4)
        self.assertEqual(error.usage.call_index, 5)
        self.assertEqual(error.scope_task_ids, [self.task.task_id])
        self.assertEqual(error.source_id, self.source.source_id)

    def test_missing_structured_output_preserves_usage(self):
        fake_client = FakeOpenAI(None)

        with self.assertRaises(ExtractorProviderError) as raised:
            self._generate(fake_client)

        error = raised.exception
        self.assertEqual(error.code, "missing_structured_output")
        self.assertIsNotNone(error.usage)
        self.assertEqual(error.usage.tokens.total_tokens, 1100)

    def test_invalid_usage_preserves_attempt_metadata(self):
        fake_client = FakeOpenAI(self._draft(), invalid_usage=True)

        with self.assertRaises(ExtractorProviderError) as raised:
            self._generate(fake_client, iteration=7, call_index=8)

        error = raised.exception
        self.assertEqual(error.code, "invalid_usage")
        self.assertIsNone(error.usage)
        self.assertEqual(error.agent, "extractor")
        self.assertEqual(error.iteration, 7)
        self.assertEqual(error.call_index, 8)
        self.assertEqual(error.requested_model, "gpt-5.6-terra")
        self.assertEqual(error.scope_task_ids, [self.task.task_id])
        self.assertEqual(error.source_id, self.source.source_id)

    def test_transport_error_does_not_expose_provider_message_or_api_key(self):
        secret = "sk-test-do-not-leak"
        fake_client = FakeOpenAI(
            self._draft(),
            error=RuntimeError(f"transport failed for {secret}"),
        )
        client = OpenAIExtractorClient(
            OpenAISettings(api_key=secret, model="gpt-5.6-terra"),
            client=fake_client,
        )

        with self.assertRaises(ExtractorProviderError) as raised:
            client.generate(
                self.plan,
                self.source,
                self.document,
                [self.task],
                [self.passage],
                "Extractor system prompt",
                iteration=1,
                call_index=1,
            )

        error = raised.exception
        self.assertEqual(error.code, "provider_exception")
        self.assertIn("RuntimeError", str(error))
        self.assertNotIn(secret, str(error))
        rendered_traceback = "".join(traceback.format_exception(error))
        self.assertNotIn(secret, rendered_traceback)
        self.assertEqual(error.scope_task_ids, [self.task.task_id])
        self.assertEqual(error.source_id, self.source.source_id)
