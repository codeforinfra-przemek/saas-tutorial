import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from datacollector.config import OpenAISettings
from datacollector.llm.openai_normalizer_client import OpenAINormalizerClient
from datacollector.llm.protocol import NormalizerProviderError
from datacollector.schemas import (
    NormalizationPrecision,
    NormalizedValueType,
    NormalizerDraft,
    NormalizerValueDraft,
)
from datacollector.tests import test_normalizer as normalizer_fixtures


class FakeResponses:
    def __init__(self, draft, *, status="completed", error=None, refusal=None):
        self.draft = draft
        self.status = status
        self.error = error
        self.refusal = refusal
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
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
            id="resp_normalizer",
            _request_id="req_normalizer",
            model="gpt-5.6-terra",
            service_tier="default",
            status=self.status,
            output=output,
            output_parsed=self.draft,
            usage=SimpleNamespace(
                input_tokens=1000,
                input_tokens_details=SimpleNamespace(
                    cached_tokens=0,
                    cache_write_tokens=0,
                ),
                output_tokens=100,
                output_tokens_details=SimpleNamespace(reasoning_tokens=20),
                total_tokens=1100,
            ),
        )


class FakeOpenAI:
    def __init__(self, draft, **kwargs):
        self.responses = FakeResponses(draft, **kwargs)


class OpenAINormalizerClientTests(TestCase):
    @classmethod
    def setUpClass(cls):
        normalizer_fixtures.NormalizerAgentTests.setUpClass()
        cls.plan = normalizer_fixtures.NormalizerAgentTests.plan
        cls.search_results = normalizer_fixtures.NormalizerAgentTests.search_results
        cls.extraction_results = (
            normalizer_fixtures.NormalizerAgentTests.extraction_results
        )
        cls.checker_results = normalizer_fixtures.NormalizerAgentTests.checker_results
        cls.claim_ids = [
            decision.claim_id
            for decision in cls.checker_results.claim_decisions
            if decision.verdict.value == "accepted"
        ]

    @classmethod
    def _draft(cls):
        claim_by_id = {
            claim.claim_id: claim for claim in cls.extraction_results.claims
        }
        return NormalizerDraft(
            values=[
                NormalizerValueDraft(
                    task_id=claim_by_id[claim_id].task_id,
                    target_field=claim_by_id[claim_id].target_field,
                    claim_ids=[claim_id],
                    value_type=NormalizedValueType.TEXT,
                    canonical_text=claim_by_id[claim_id].value_text,
                    precision=NormalizationPrecision.EXACT,
                )
                for claim_id in cls.claim_ids
            ]
        )

    def _generate(self, fake_client, *, model="gpt-5.6-terra"):
        client = OpenAINormalizerClient(
            OpenAISettings(api_key="test", model=model),
            client=fake_client,
        )
        return client.generate(
            self.plan,
            self.search_results,
            self.extraction_results,
            self.checker_results,
            self.claim_ids,
            "Normalizer system prompt",
            iteration=4,
            call_index=1,
        )

    @patch("datacollector.llm.openai_normalizer_client.OpenAI")
    def test_constructor_disables_hidden_sdk_retries(self, openai):
        settings = OpenAISettings(
            api_key="test",
            model="gpt-5.6-terra",
            timeout_seconds=41,
            max_retries=9,
        )

        OpenAINormalizerClient(settings)

        openai.assert_called_once_with(
            api_key="test",
            timeout=41,
            max_retries=0,
        )

    def test_request_contains_only_accepted_claims_and_has_no_tools(self):
        draft = self._draft()
        fake_client = FakeOpenAI(draft)

        generation = self._generate(fake_client)

        request = fake_client.responses.kwargs
        self.assertIs(request["text_format"], NormalizerDraft)
        self.assertNotIn("tools", request)
        self.assertFalse(request["store"])
        self.assertEqual(
            request["prompt_cache_options"],
            {"mode": "explicit"},
        )
        payload = json.loads(request["input"][1]["content"])
        self.assertEqual(
            [item["claim_id"] for item in payload["accepted_claims"]],
            self.claim_ids,
        )
        self.assertEqual(payload["required_output"]["claim_ids"], self.claim_ids)
        self.assertIs(generation.draft, draft)
        self.assertEqual(generation.usage.agent, "normalizer")
        self.assertEqual(generation.usage.tool_usage, [])

    def test_explicit_cache_mode_is_not_sent_to_older_models(self):
        fake_client = FakeOpenAI(self._draft())

        self._generate(fake_client, model="gpt-5.5-terra")

        self.assertNotIn("prompt_cache_options", fake_client.responses.kwargs)

    def test_refusal_preserves_usage_without_exposing_refusal_text(self):
        secret = "private fixture refusal"
        fake_client = FakeOpenAI(None, refusal=secret)

        with self.assertRaises(NormalizerProviderError) as raised:
            self._generate(fake_client)

        self.assertEqual(raised.exception.code, "refusal")
        self.assertIsNotNone(raised.exception.usage)
        self.assertNotIn(secret, str(raised.exception))
