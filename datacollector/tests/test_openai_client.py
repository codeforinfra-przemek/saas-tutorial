import json
from decimal import Decimal
import traceback
from types import SimpleNamespace
from unittest import TestCase

from datacollector.catalog import load_question_catalog
from datacollector.config import OpenAISettings
from datacollector.llm.openai_client import OpenAIPlannerClient, PlannerProviderError
from datacollector.schemas import PlannerDraft, PlannerInput


class FakeResponses:
    def __init__(self, output, error=None, usage=None):
        self.output = output
        self.error = error
        self.usage = usage or SimpleNamespace(
            input_tokens=1200,
            input_tokens_details=SimpleNamespace(
                cached_tokens=200,
                cache_write_tokens=100,
            ),
            output_tokens=100,
            output_tokens_details=SimpleNamespace(reasoning_tokens=40),
            total_tokens=1300,
        )
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return SimpleNamespace(
            id="resp_test",
            _request_id="req_test",
            model="gpt-5.6-terra",
            service_tier="default",
            output_parsed=self.output,
            usage=self.usage,
        )


class FakeOpenAI:
    def __init__(self, output=None, error=None, usage=None):
        self.responses = FakeResponses(output, error, usage)


class OpenAIPlannerClientTests(TestCase):
    def test_responses_api_uses_structured_output_schema(self):
        draft = PlannerDraft(
            objective="Create a sufficiently detailed research plan.",
            planning_notes=[],
            assumptions=[],
            scope_warnings=[],
            task_guidance=[],
        )
        fake_client = FakeOpenAI(draft)
        client = OpenAIPlannerClient(
            OpenAISettings(api_key="test", model="gpt-5.6-terra"),
            client=fake_client,
        )

        result = client.generate(
            PlannerInput(brand_name="Example"),
            [],
            "Planner system prompt",
            iteration=3,
        )

        self.assertIs(result.draft, draft)
        self.assertEqual(fake_client.responses.kwargs["model"], "gpt-5.6-terra")
        self.assertIs(fake_client.responses.kwargs["text_format"], PlannerDraft)
        self.assertEqual(
            fake_client.responses.kwargs["reasoning"], {"effort": "medium"}
        )
        self.assertEqual(fake_client.responses.kwargs["max_output_tokens"], 8000)
        self.assertFalse(fake_client.responses.kwargs["store"])
        self.assertEqual(
            fake_client.responses.kwargs["input"][0]["role"], "system"
        )
        self.assertEqual(
            fake_client.responses.kwargs["metadata"],
            {"agent": "planner", "iteration": "3"},
        )
        self.assertEqual(
            fake_client.responses.kwargs["prompt_cache_options"],
            {"mode": "explicit"},
        )
        self.assertEqual(result.usage.iteration, 3)
        self.assertEqual(result.usage.response_id, "resp_test")
        self.assertEqual(result.usage.request_id, "req_test")
        self.assertEqual(result.usage.tokens.cached_input_tokens, 200)
        self.assertEqual(result.usage.tokens.cache_write_input_tokens, 100)
        self.assertEqual(result.usage.tokens.reasoning_tokens, 40)
        self.assertEqual(
            result.usage.cost_estimate.total_estimated_cost_usd,
            Decimal("0.00411250"),
        )

    def test_prompt_cache_options_are_not_sent_to_older_models(self):
        draft = PlannerDraft(
            objective="Create a sufficiently detailed research plan.",
            planning_notes=[],
            assumptions=[],
            scope_warnings=[],
            task_guidance=[],
        )
        fake_client = FakeOpenAI(draft)
        client = OpenAIPlannerClient(
            OpenAISettings(api_key="test", model="test-model"),
            client=fake_client,
        )

        client.generate(
            PlannerInput(brand_name="Example"),
            [],
            "Planner system prompt",
            iteration=1,
        )

        self.assertNotIn("prompt_cache_options", fake_client.responses.kwargs)

    def test_provider_payload_excludes_deterministic_evidence_metadata(self):
        question = load_question_catalog().all_questions()[0]
        draft = PlannerDraft(
            objective="Create a sufficiently detailed research plan.",
            planning_notes=[],
            assumptions=[],
            scope_warnings=[],
            task_guidance=[],
        )
        fake_client = FakeOpenAI(draft)
        client = OpenAIPlannerClient(
            OpenAISettings(api_key="test", model="gpt-5.6-terra"),
            client=fake_client,
        )

        client.generate(
            PlannerInput(brand_name="Example"),
            [question],
            "Planner system prompt",
            iteration=1,
        )
        payload = json.loads(fake_client.responses.kwargs["input"][1]["content"])
        sent_question = payload["canonical_questions"][0]

        self.assertRegex(
            payload["planning_context"]["current_date"],
            r"^\d{4}-\d{2}-\d{2}$",
        )
        self.assertEqual(sent_question["id"], question.id)
        self.assertIn("preferred_source_types", sent_question)
        self.assertNotIn("evidence", sent_question)
        self.assertNotIn("acceptance_criteria", sent_question)

    def test_missing_parsed_output_is_reported_as_provider_error(self):
        client = OpenAIPlannerClient(
            OpenAISettings(api_key="secret", model="test-model"),
            client=FakeOpenAI(output=None),
        )

        with self.assertRaisesRegex(PlannerProviderError, "structured output"):
            client.generate(
                PlannerInput(brand_name="Example"),
                [],
                "Planner system prompt",
                iteration=1,
            )

    def test_transport_error_does_not_expose_provider_message_or_api_key(self):
        secret = "sk-test-do-not-leak"
        client = OpenAIPlannerClient(
            OpenAISettings(api_key=secret, model="test-model"),
            client=FakeOpenAI(error=RuntimeError(f"transport failed for {secret}")),
        )

        with self.assertRaises(PlannerProviderError) as raised:
            client.generate(
                PlannerInput(brand_name="Example"),
                [],
                "Planner system prompt",
                iteration=1,
            )

        self.assertIn("RuntimeError", str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))
        rendered_traceback = "".join(
            traceback.format_exception(raised.exception)
        )
        self.assertNotIn(secret, rendered_traceback)
