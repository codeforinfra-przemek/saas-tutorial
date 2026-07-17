import traceback
from types import SimpleNamespace
from unittest import TestCase

from datacollector.config import OpenAISettings
from datacollector.llm.openai_client import OpenAIPlannerClient, PlannerProviderError
from datacollector.schemas import PlannerDraft, PlannerInput


class FakeResponses:
    def __init__(self, output, error=None):
        self.output = output
        self.error = error
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return SimpleNamespace(output_parsed=self.output)


class FakeOpenAI:
    def __init__(self, output=None, error=None):
        self.responses = FakeResponses(output, error)


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
            OpenAISettings(api_key="test", model="test-model"),
            client=fake_client,
        )

        result = client.generate(
            PlannerInput(brand_name="Example"),
            [],
            "Planner system prompt",
        )

        self.assertIs(result, draft)
        self.assertEqual(fake_client.responses.kwargs["model"], "test-model")
        self.assertIs(fake_client.responses.kwargs["text_format"], PlannerDraft)
        self.assertEqual(
            fake_client.responses.kwargs["reasoning"], {"effort": "medium"}
        )
        self.assertEqual(fake_client.responses.kwargs["max_output_tokens"], 8000)
        self.assertFalse(fake_client.responses.kwargs["store"])
        self.assertEqual(
            fake_client.responses.kwargs["input"][0]["role"], "system"
        )

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
            )

        self.assertIn("RuntimeError", str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))
        rendered_traceback = "".join(
            traceback.format_exception(raised.exception)
        )
        self.assertNotIn(secret, rendered_traceback)
