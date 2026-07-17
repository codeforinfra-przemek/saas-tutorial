import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from datacollector.cli import main
from datacollector.config import OpenAISettings
from datacollector.llm.openai_searcher_client import SearcherProviderError
from datacollector.llm.pricing import build_web_search_tool_usage
from datacollector.schemas import AgentIterationUsage, TokenUsage


class FailingPaidSearcher:
    model_name = "gpt-5.6-terra"

    def generate(self, *args, iteration, **kwargs):
        usage = AgentIterationUsage(
            agent="searcher",
            iteration=iteration,
            requested_model=self.model_name,
            resolved_model=self.model_name,
            response_id="resp_failed_test",
            tokens=TokenUsage(
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
            ),
        )
        raise SearcherProviderError(
            "Paid response was unusable.",
            code="missing_structured_output",
            usage=usage,
        )


class FailingMultiUsageSearcher:
    model_name = "gpt-5.6-terra"

    def generate(self, *args, iteration, **kwargs):
        usages = [
            AgentIterationUsage(
                agent="searcher",
                iteration=iteration,
                call_index=call_index,
                requested_model=self.model_name,
                resolved_model=self.model_name,
                response_id=f"resp_failed_{call_index}",
                tokens=TokenUsage(
                    input_tokens=100,
                    output_tokens=20,
                    total_tokens=120,
                ),
            )
            for call_index in (1, 2)
        ]
        raise SearcherProviderError(
            "Local processing failed after two paid responses.",
            code="postprocessing_error",
            usages=usages,
        )


class MissingTokenUsageSearcher:
    model_name = "gpt-5.6-terra"

    def generate(
        self,
        plan,
        tasks,
        system_prompt,
        *,
        iteration,
        call_index,
        **kwargs,
    ):
        raise SearcherProviderError(
            "Provider omitted token usage after a search action.",
            code="invalid_usage",
            observed_tool_calls=1,
            tool_usage=[build_web_search_tool_usage({"search": 1})],
            agent="searcher",
            iteration=iteration,
            call_index=call_index,
            scope_task_ids=[task.task_id for task in tasks],
            requested_model=self.model_name,
        )


class CollectorCliTests(TestCase):
    def test_offline_plan_command_creates_artifact_without_api(self):
        with TemporaryDirectory() as temporary_directory:
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "plan",
                        "--brand",
                        "Żabka",
                        "--country",
                        "PL",
                        "--depth",
                        "catalog",
                        "--offline",
                        "--output-dir",
                        temporary_directory,
                    ]
                )

            summary = json.loads(stdout.getvalue())
            plan_paths = list(Path(temporary_directory).rglob("plan-free.json"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(summary["generated_by"], "offline")
            self.assertEqual(summary["agent_usage"], [])
            self.assertEqual(len(plan_paths), 1)

    def test_free_search_command_creates_sources_free_beside_exact_plan(self):
        with TemporaryDirectory() as temporary_directory:
            plan_stdout = StringIO()
            with redirect_stdout(plan_stdout):
                self.assertEqual(
                    main(
                        [
                            "plan",
                            "--brand",
                            "Żabka",
                            "--depth",
                            "catalog",
                            "--free",
                            "--output-dir",
                            temporary_directory,
                        ]
                    ),
                    0,
                )
            plan_path = Path(json.loads(plan_stdout.getvalue())["plan_path"])

            search_stdout = StringIO()
            with redirect_stdout(search_stdout):
                exit_code = main(
                    [
                        "search",
                        "--plan",
                        str(plan_path),
                        "--free",
                        "--limit-tasks",
                        "2",
                    ]
                )

            summary = json.loads(search_stdout.getvalue())
            sources_path = Path(summary["sources_path"])
            self.assertEqual(exit_code, 0)
            self.assertEqual(summary["generated_by"], "offline")
            self.assertEqual(summary["selected_tasks"], 2)
            self.assertEqual(summary["agent_usage"], [])
            self.assertEqual(summary["provider_observed_sources"], 0)
            self.assertEqual(summary["provider_verified_sources"], 0)
            self.assertEqual(summary["plan_seed_sources"], 0)
            self.assertEqual(summary["task_query_coverage"], {"workload_only": 2})
            self.assertEqual(summary["usage_totals"]["total_tokens"], 0)
            self.assertEqual(summary["usage_totals"]["api_attempts_recorded"], 0)
            self.assertEqual(summary["usage_totals"]["tool_calls"], 0)
            self.assertEqual(summary["usage_totals"]["estimated_cost_usd"], "0")
            self.assertEqual(sources_path.name, "sources-free.json")
            self.assertEqual(sources_path.parent, plan_path.parent)
            self.assertTrue(sources_path.exists())

            stderr = StringIO()
            with redirect_stderr(stderr):
                duplicate_exit_code = main(
                    [
                        "search",
                        "--plan",
                        str(plan_path),
                        "--free",
                        "--limit-tasks",
                        "2",
                    ]
                )
            self.assertEqual(duplicate_exit_code, 2)
            self.assertIn("will not be overwritten", stderr.getvalue())

    def test_questions_command_is_read_only(self):
        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = main(
                ["questions", "--country", "US", "--depth", "due_diligence"]
            )

        payload = json.loads(stdout.getvalue())
        question_ids = {question["id"] for question in payload["questions"]}
        self.assertEqual(exit_code, 0)
        self.assertIn("us.state_overlay", question_ids)
        self.assertGreater(payload["question_count"], 20)

    def test_paid_failure_usage_is_saved_and_output_reservation_is_released(self):
        with TemporaryDirectory() as temporary_directory:
            plan_stdout = StringIO()
            with redirect_stdout(plan_stdout):
                self.assertEqual(
                    main(
                        [
                            "plan",
                            "--brand",
                            "Example",
                            "--depth",
                            "catalog",
                            "--free",
                            "--output-dir",
                            temporary_directory,
                        ]
                    ),
                    0,
                )
            plan_path = Path(json.loads(plan_stdout.getvalue())["plan_path"])
            stderr = StringIO()
            with (
                patch.object(
                    OpenAISettings,
                    "from_env",
                    return_value=OpenAISettings(
                        api_key="test",
                        model="gpt-5.6-terra",
                    ),
                ),
                patch(
                    "datacollector.cli.OpenAISearcherClient",
                    return_value=FailingPaidSearcher(),
                ),
                redirect_stderr(stderr),
            ):
                exit_code = main(
                    [
                        "search",
                        "--plan",
                        str(plan_path),
                        "--limit-tasks",
                        "1",
                    ]
                )

            failure_paths = list((plan_path.parent / "attempts").glob("*.json"))
            self.assertEqual(exit_code, 2)
            self.assertEqual(len(failure_paths), 1)
            self.assertFalse((plan_path.parent / "sources.json").exists())
            self.assertFalse((plan_path.parent / ".sources.json.lock").exists())
            self.assertIn("Provider usage saved", stderr.getvalue())

    def test_all_paid_usages_are_saved_when_postprocessing_fails(self):
        with TemporaryDirectory() as temporary_directory:
            plan_stdout = StringIO()
            with redirect_stdout(plan_stdout):
                self.assertEqual(
                    main(
                        [
                            "plan",
                            "--brand",
                            "Example",
                            "--depth",
                            "catalog",
                            "--free",
                            "--output-dir",
                            temporary_directory,
                        ]
                    ),
                    0,
                )
            plan_path = Path(json.loads(plan_stdout.getvalue())["plan_path"])

            with (
                patch.object(
                    OpenAISettings,
                    "from_env",
                    return_value=OpenAISettings(
                        api_key="test",
                        model="gpt-5.6-terra",
                    ),
                ),
                patch(
                    "datacollector.cli.OpenAISearcherClient",
                    return_value=FailingMultiUsageSearcher(),
                ),
                redirect_stderr(StringIO()),
            ):
                exit_code = main(
                    [
                        "search",
                        "--plan",
                        str(plan_path),
                        "--limit-tasks",
                        "1",
                    ]
                )

            failure_paths = sorted((plan_path.parent / "attempts").glob("*.json"))
            self.assertEqual(exit_code, 2)
            self.assertEqual(len(failure_paths), 2)
            self.assertIn("-c001-", failure_paths[0].name)
            self.assertIn("-c002-", failure_paths[1].name)

    def test_known_tool_cost_is_saved_when_token_usage_is_missing(self):
        with TemporaryDirectory() as temporary_directory:
            plan_stdout = StringIO()
            with redirect_stdout(plan_stdout):
                self.assertEqual(
                    main(
                        [
                            "plan",
                            "--brand",
                            "Example",
                            "--depth",
                            "catalog",
                            "--free",
                            "--output-dir",
                            temporary_directory,
                        ]
                    ),
                    0,
                )
            plan_path = Path(json.loads(plan_stdout.getvalue())["plan_path"])

            with (
                patch.object(
                    OpenAISettings,
                    "from_env",
                    return_value=OpenAISettings(
                        api_key="test",
                        model="gpt-5.6-terra",
                    ),
                ),
                patch(
                    "datacollector.cli.OpenAISearcherClient",
                    return_value=MissingTokenUsageSearcher(),
                ),
                redirect_stderr(StringIO()),
            ):
                exit_code = main(
                    [
                        "search",
                        "--plan",
                        str(plan_path),
                        "--limit-tasks",
                        "1",
                    ]
                )

            failure_path = next((plan_path.parent / "attempts").glob("*.json"))
            failure = json.loads(failure_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 2)
            self.assertIsNone(failure["usage"])
            self.assertTrue(failure["token_usage_unknown"])
            self.assertEqual(failure["observed_tool_calls"], 1)
            self.assertEqual(failure["tool_usage"][0]["calls"], 1)
            self.assertEqual(
                failure["tool_usage"][0]["estimated_cost_usd"],
                "0.01",
            )
