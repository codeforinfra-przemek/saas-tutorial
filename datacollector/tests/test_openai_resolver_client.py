import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from datacollector.config import OpenAISettings
from datacollector.llm.openai_resolver_client import OpenAIResolverClient
from datacollector.llm.protocol import ResolverProviderError
from datacollector.schemas import ResolverDraft, ResolverItemDraft
from datacollector.tests import test_resolver as resolver_fixtures


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
            id="resp_resolver",
            _request_id="req_resolver",
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


class OpenAIResolverClientTests(TestCase):
    @classmethod
    def setUpClass(cls):
        resolver_fixtures.ResolverAgentTests.setUpClass()
        case = resolver_fixtures.ResolverAgentTests(
            methodName=(
                "test_free_reextracts_only_usable_unprocessed_evidence_sources"
            )
        )
        cls.plan = case.plan
        cls.search_results = case.search_results
        cls.checker_results = case.checker_results
        cls.work_items = case._run().work_items

    @classmethod
    def _draft(cls):
        return ResolverDraft(
            items=[
                ResolverItemDraft(
                    follow_up_id=item.follow_up_id,
                    selected_action=item.selected_action,
                    selected_source_ids=item.selected_source_ids,
                    sequence=item.sequence,
                    rationale="The fixture retains the bounded local action.",
                )
                for item in cls.work_items
            ]
        )

    def _generate(self, fake_client, *, model="gpt-5.6-terra"):
        client = OpenAIResolverClient(
            OpenAISettings(api_key="test", model=model),
            client=fake_client,
        )
        return client.generate(
            self.plan,
            self.search_results,
            self.checker_results,
            self.work_items,
            "Resolver system prompt",
            iteration=4,
            call_index=1,
        )

    @patch("datacollector.llm.openai_resolver_client.OpenAI")
    def test_constructor_disables_hidden_sdk_retries(self, openai):
        settings = OpenAISettings(
            api_key="test",
            model="gpt-5.6-terra",
            timeout_seconds=41,
            max_retries=9,
        )

        OpenAIResolverClient(settings)

        openai.assert_called_once_with(
            api_key="test",
            timeout=41,
            max_retries=0,
        )

    def test_request_is_structured_bounded_and_has_no_tools(self):
        draft = self._draft()
        fake_client = FakeOpenAI(draft)

        generation = self._generate(fake_client)

        request = fake_client.responses.kwargs
        self.assertEqual(request["model"], "gpt-5.6-terra")
        self.assertIs(request["text_format"], ResolverDraft)
        self.assertEqual(
            request["prompt_cache_options"],
            {"mode": "explicit"},
        )
        self.assertNotIn("tools", request)
        self.assertFalse(request["store"])
        payload = json.loads(request["input"][1]["content"])
        self.assertEqual(
            payload["required_output"]["follow_up_ids"],
            [item.follow_up_id for item in self.work_items],
        )
        self.assertEqual(
            payload["work_items"][0]["allowed_actions"],
            [action.value for action in self.work_items[0].allowed_actions],
        )
        self.assertIs(generation.draft, draft)
        self.assertEqual(generation.usage.agent, "resolver")
        self.assertEqual(generation.usage.tool_usage, [])

    def test_explicit_cache_mode_is_not_sent_to_older_models(self):
        fake_client = FakeOpenAI(self._draft())

        self._generate(fake_client, model="gpt-5.5-terra")

        self.assertNotIn("prompt_cache_options", fake_client.responses.kwargs)

    def test_refusal_preserves_usage_without_exposing_refusal_text(self):
        secret = "private fixture refusal"
        fake_client = FakeOpenAI(None, refusal=secret)

        with self.assertRaises(ResolverProviderError) as raised:
            self._generate(fake_client)

        self.assertEqual(raised.exception.code, "refusal")
        self.assertIsNotNone(raised.exception.usage)
        self.assertNotIn(secret, str(raised.exception))
