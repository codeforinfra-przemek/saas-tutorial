import json
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase

from datacollector.agents.planner import PlannerAgent
from datacollector.catalog import load_question_catalog
from datacollector.config import OpenAISettings
from datacollector.llm.openai_searcher_client import (
    OpenAISearcherClient,
    SearcherProviderError,
    _as_mapping,
)
from datacollector.schemas import PlannerInput, SearcherDraft


class FakeResponses:
    def __init__(
        self,
        draft,
        *,
        include_action=True,
        output_override=None,
        response_status="completed",
        invalid_usage=False,
    ):
        self.draft = draft
        self.include_action = include_action
        self.output_override = output_override
        self.response_status = response_status
        self.invalid_usage = invalid_usage
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        output = []
        if self.include_action:
            output.append(
                {
                    "type": "web_search_call",
                    "id": "ws_test",
                    "action": {
                        "type": "search",
                        "queries": ["Example franchise official website"],
                        "sources": [
                            {
                                "url": "https://example.com/franchise",
                                "title": "Example franchise",
                            }
                        ],
                    },
                }
            )
        output.append(
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Structured output follows.",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://example.com/franchise",
                                "title": "Example franchise",
                            }
                        ],
                    }
                ],
            }
        )
        if self.output_override is not None:
            output = self.output_override
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
            id="resp_search",
            _request_id="req_search",
            model="gpt-5.6-terra",
            service_tier="default",
            status=self.response_status,
            output_parsed=self.draft,
            output=output,
            usage=usage,
        )


class FakeOpenAI:
    def __init__(
        self,
        draft,
        *,
        include_action=True,
        output_override=None,
        response_status="completed",
        invalid_usage=False,
    ):
        self.responses = FakeResponses(
            draft,
            include_action=include_action,
            output_override=output_override,
            response_status=response_status,
            invalid_usage=invalid_usage,
        )


class OpenAISearcherClientTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = PlannerAgent(load_question_catalog()).create_plan(
            PlannerInput(brand_name="Example", target_country="PL", depth="catalog")
        )

    def test_searcher_uses_required_web_search_and_records_full_cost(self):
        draft = SearcherDraft(warnings=[], sources=[], task_results=[])
        fake_client = FakeOpenAI(draft)
        client = OpenAISearcherClient(
            OpenAISettings(api_key="test", model="gpt-5.6-terra"),
            client=fake_client,
        )

        generation = client.generate(
            self.plan,
            [self.plan.tasks[0]],
            "Searcher system prompt",
            iteration=2,
            call_index=3,
            max_search_calls=4,
            min_queries_per_task=1,
        )

        request = fake_client.responses.kwargs
        self.assertEqual(request["tools"][0]["type"], "web_search")
        self.assertEqual(request["tools"][0]["search_context_size"], "low")
        self.assertEqual(
            request["tools"][0]["filters"]["blocked_domains"],
            ["arxiv.org", "quora.com", "reddit.com", "wikipedia.org"],
        )
        self.assertTrue(request["tools"][0]["external_web_access"])
        self.assertEqual(
            request["tools"][0]["user_location"]["country"],
            "PL",
        )
        self.assertEqual(request["tool_choice"], "required")
        self.assertEqual(request["max_tool_calls"], 4)
        self.assertEqual(request["metadata"]["call_index"], "3")
        self.assertEqual(
            request["include"],
            ["web_search_call.action.sources"],
        )
        self.assertFalse(request["store"])
        self.assertEqual(
            request["prompt_cache_options"],
            {"mode": "explicit"},
        )
        self.assertIs(request["text_format"], SearcherDraft)
        payload = json.loads(request["input"][1]["content"])
        self.assertRegex(
            payload["search_context"]["current_date"],
            r"^\d{4}-\d{2}-\d{2}$",
        )
        self.assertEqual(
            [item["task_id"] for item in payload["tasks"]],
            [self.plan.tasks[0].task_id],
        )
        self.assertIn("source_hints", payload["tasks"][0])
        self.assertIn("acceptance_criteria", payload["tasks"][0])
        self.assertIn("depends_on", payload["tasks"][0])
        self.assertEqual(payload["tasks"][0]["minimum_query_attempts"], 1)
        self.assertEqual(len(generation.actions), 1)
        self.assertEqual(generation.actions[0].call_index, 3)
        self.assertEqual(
            generation.actions[0].scope_task_ids,
            [self.plan.tasks[0].task_id],
        )
        self.assertEqual(len(generation.provider_sources), 1)
        self.assertEqual(generation.usage.agent, "searcher")
        self.assertEqual(generation.usage.iteration, 2)
        self.assertEqual(generation.usage.call_index, 3)
        self.assertEqual(
            generation.usage.scope_task_ids,
            [self.plan.tasks[0].task_id],
        )
        self.assertEqual(generation.usage.tool_usage[0].calls, 1)
        self.assertEqual(
            generation.usage.cost_estimate.tool_cost_usd,
            Decimal("0.01000000"),
        )
        self.assertEqual(
            generation.usage.cost_estimate.total_estimated_cost_usd,
            Decimal("0.01400000"),
        )

    def test_sdk_mapping_does_not_serialize_structured_parsed_union(self):
        class SDKLikeObject:
            def __init__(self):
                self.type = "message"
                self.parsed = SearcherDraft(warnings=[], sources=[], task_results=[])

            def model_dump(self, **kwargs):
                raise AssertionError("full SDK serialization must not be used")

        self.assertEqual(_as_mapping(SDKLikeObject())["type"], "message")

    def test_empty_domain_block_list_omits_filters(self):
        fake_client = FakeOpenAI(
            SearcherDraft(warnings=[], sources=[], task_results=[])
        )
        client = OpenAISearcherClient(
            OpenAISettings(
                api_key="test",
                web_search_blocked_domains=(),
            ),
            client=fake_client,
        )

        client.generate(
            self.plan,
            [self.plan.tasks[0]],
            "Searcher system prompt",
            iteration=1,
            call_index=1,
            max_search_calls=2,
            min_queries_per_task=1,
        )

        self.assertNotIn("filters", fake_client.responses.kwargs["tools"][0])

    def test_missing_web_search_action_is_rejected(self):
        client = OpenAISearcherClient(
            OpenAISettings(api_key="test", model="gpt-5.6-terra"),
            client=FakeOpenAI(
                SearcherDraft(warnings=[], sources=[], task_results=[]),
                include_action=False,
            ),
        )

        with self.assertRaisesRegex(
            SearcherProviderError, "web search action"
        ) as raised:
            client.generate(
                self.plan,
                [self.plan.tasks[0]],
                "Searcher system prompt",
                iteration=1,
                call_index=1,
                max_search_calls=2,
                min_queries_per_task=1,
            )

        self.assertIsNotNone(raised.exception.usage)
        self.assertEqual(raised.exception.observed_tool_calls, 0)
        self.assertEqual(raised.exception.usage.tokens.total_tokens, 1100)

    def test_completed_open_page_url_is_retained_as_provider_source(self):
        output = [
            {
                "type": "web_search_call",
                "id": "ws_search",
                "status": "completed",
                "action": {
                    "type": "search",
                    "query": "Example franchise",
                    "sources": [],
                },
            },
            {
                "type": "web_search_call",
                "id": "ws_open",
                "status": "completed",
                "action": {
                    "type": "open_page",
                    "url": "https://example.com/opened-document",
                    "sources": [],
                },
            },
        ]
        client = OpenAISearcherClient(
            OpenAISettings(api_key="test", model="gpt-5.6-terra"),
            client=FakeOpenAI(
                SearcherDraft(warnings=[], sources=[], task_results=[]),
                output_override=output,
            ),
        )

        generation = client.generate(
            self.plan,
            [self.plan.tasks[0]],
            "Searcher system prompt",
            iteration=1,
            call_index=1,
            max_search_calls=3,
            min_queries_per_task=1,
        )

        self.assertIn(
            "https://example.com/opened-document",
            [source.url for source in generation.provider_sources],
        )
        self.assertEqual(generation.usage.tool_usage[0].calls, 1)

    def test_incomplete_response_is_rejected_with_usage_for_failure_ledger(self):
        client = OpenAISearcherClient(
            OpenAISettings(api_key="test", model="gpt-5.6-terra"),
            client=FakeOpenAI(
                SearcherDraft(warnings=[], sources=[], task_results=[]),
                response_status="incomplete",
            ),
        )

        with self.assertRaisesRegex(
            SearcherProviderError, "status 'incomplete'"
        ) as raised:
            client.generate(
                self.plan,
                [self.plan.tasks[0]],
                "Searcher system prompt",
                iteration=1,
                call_index=1,
                max_search_calls=2,
                min_queries_per_task=1,
            )

        self.assertEqual(raised.exception.code, "incomplete_response")
        self.assertIsNotNone(raised.exception.usage)
        self.assertEqual(raised.exception.observed_tool_calls, 1)

    def test_invalid_token_usage_keeps_known_tool_cost_and_attempt_metadata(self):
        client = OpenAISearcherClient(
            OpenAISettings(api_key="test", model="gpt-5.6-terra"),
            client=FakeOpenAI(
                SearcherDraft(warnings=[], sources=[], task_results=[]),
                invalid_usage=True,
            ),
        )

        with self.assertRaises(SearcherProviderError) as raised:
            client.generate(
                self.plan,
                [self.plan.tasks[0]],
                "Searcher system prompt",
                iteration=4,
                call_index=2,
                max_search_calls=2,
                min_queries_per_task=1,
            )

        error = raised.exception
        self.assertEqual(error.code, "invalid_usage")
        self.assertIsNone(error.usage)
        self.assertEqual(error.observed_tool_calls, 1)
        self.assertEqual(error.tool_usage[0].estimated_cost_usd, Decimal("0.01"))
        self.assertEqual(error.agent, "searcher")
        self.assertEqual(error.iteration, 4)
        self.assertEqual(error.call_index, 2)
        self.assertEqual(error.requested_model, "gpt-5.6-terra")
