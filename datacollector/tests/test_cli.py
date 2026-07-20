import hashlib
import json
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from datacollector.agents.planner import PlannerAgent
from datacollector.agents.searcher import SearcherAgent
from datacollector.catalog import load_question_catalog
from datacollector.cli import main
from datacollector.config import OpenAISettings
from datacollector.documents import FetchedDocument, FetchStatus
from datacollector.llm.protocol import (
    CheckerGeneration,
    CheckerProviderError,
    ExtractorGeneration,
    ExtractorProviderError,
    ProviderSearchSource,
    SearcherGeneration,
)
from datacollector.llm.openai_searcher_client import SearcherProviderError
from datacollector.llm.pricing import (
    build_web_search_tool_usage,
    estimate_standard_token_cost,
)
from datacollector.schemas import (
    AgentIterationUsage,
    CheckerClaimDecisionDraft,
    CheckerDraft,
    CheckerModelSemanticFit,
    CheckerModelSourceSupport,
    CheckerModelVerdict,
    ExtractionAttemptFailure,
    ExtractionConfidence,
    ExtractorClaimDraft,
    ExtractorDraft,
    PlannerInput,
    SearchAction,
    SearchTaskStatus,
    SearcherDraft,
    SearcherSourceDraft,
    SearcherTaskDraft,
    SourceType,
    TokenUsage,
)
from datacollector.storage.json_store import (
    load_checker_results,
    load_extraction_results,
    load_research_plan,
    save_research_plan,
    save_search_results,
)


CLI_DOCUMENT_VALUE = "Example Polska sp. z o.o."
CLI_DOCUMENT_TEXT = (
    "Brand identity and official franchise website. The exact legal franchisor "
    f"is {CLI_DOCUMENT_VALUE}, registered as the operator in Poland."
)


class CliFixtureSearcher:
    model_name = "gpt-5.6-terra"

    def generate(
        self,
        plan,
        tasks,
        system_prompt,
        *,
        iteration,
        call_index,
        max_search_calls,
        min_queries_per_task,
    ):
        del plan, system_prompt, max_search_calls, min_queries_per_task
        task = tasks[0]
        query = task.search_queries[0]
        url = "https://example.com/franchise"
        return SearcherGeneration(
            draft=SearcherDraft(
                warnings=[],
                sources=[
                    SearcherSourceDraft(
                        url=url,
                        title="Official franchise page",
                        source_type=SourceType.OFFICIAL,
                        task_ids=[task.task_id],
                        relevance_note=(
                            "Official page relevant to legal identity target fields."
                        ),
                    )
                ],
                task_results=[
                    SearcherTaskDraft(
                        task_id=task.task_id,
                        status=SearchTaskStatus.PARTIAL,
                        attempted_queries=[query],
                        source_urls=[url],
                        unresolved_targets=["Official registry extract"],
                        notes="One provider-grounded source candidate.",
                    )
                ],
            ),
            usage=AgentIterationUsage(
                agent="searcher",
                iteration=iteration,
                call_index=call_index,
                scope_task_ids=[task.task_id],
                requested_model=self.model_name,
                resolved_model=self.model_name,
                tokens=TokenUsage(
                    input_tokens=100,
                    output_tokens=20,
                    total_tokens=120,
                ),
                tool_usage=[build_web_search_tool_usage({"search": 1})],
            ),
            actions=[
                SearchAction(
                    action_id="ws_cli_fixture",
                    call_index=call_index,
                    scope_task_ids=[task.task_id],
                    action_type="search",
                    status="completed",
                    queries=[query],
                    source_urls=[url],
                )
            ],
            provider_sources=[
                ProviderSearchSource(
                    url=url,
                    title="Official franchise page",
                )
            ],
        )


class RecordingDocumentFetcher:
    def __init__(self):
        self.calls = []

    def fetch(self, url, *, source_id=""):
        self.calls.append((url, source_id))
        content = CLI_DOCUMENT_TEXT.encode("utf-8")
        return FetchedDocument(
            source_id=source_id,
            requested_url=url,
            final_url=url,
            status=FetchStatus.FETCHED,
            fetched_at=datetime.now(timezone.utc),
            http_status=200,
            media_type="text/html",
            content=content,
            text=CLI_DOCUMENT_TEXT,
            title="Official franchise page",
            byte_count=len(content),
            content_sha256=hashlib.sha256(content).hexdigest(),
            text_sha256=hashlib.sha256(content).hexdigest(),
        )


class CliFixtureExtractor:
    model_name = "gpt-5.6-terra"

    def __init__(self):
        self.calls = []

    def generate(
        self,
        plan,
        source,
        document,
        tasks,
        passages,
        system_prompt,
        *,
        iteration,
        call_index,
    ):
        self.calls.append(
            (
                plan,
                source,
                document,
                tasks,
                passages,
                system_prompt,
                iteration,
                call_index,
            )
        )
        task = next(task for task in tasks if task.task_id in source.task_ids)
        passage = next(
            passage for passage in passages if passage.task_id == task.task_id
        )
        tokens = TokenUsage(
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
        )
        usage = AgentIterationUsage(
            agent="extractor",
            iteration=iteration,
            call_index=call_index,
            scope_task_ids=[task.task_id],
            scope_source_ids=[source.source_id],
            requested_model=self.model_name,
            resolved_model=self.model_name,
            service_tier="default",
            tokens=tokens,
            cost_estimate=estimate_standard_token_cost(
                self.model_name,
                tokens,
                service_tier="default",
            ),
        )
        return ExtractorGeneration(
            draft=ExtractorDraft(
                claims=[
                    ExtractorClaimDraft(
                        task_id=task.task_id,
                        target_field=task.target_fields[0],
                        passage_id=passage.passage_id,
                        value_text=CLI_DOCUMENT_VALUE,
                        evidence_quote=CLI_DOCUMENT_VALUE,
                        confidence=ExtractionConfidence.HIGH,
                    )
                ],
                warnings=[],
            ),
            usage=usage,
            source_id=source.source_id,
        )


class CliFixtureChecker:
    model_name = "gpt-5.6-terra"

    def __init__(self):
        self.calls = []

    def generate(
        self,
        plan,
        search_results,
        extraction_results,
        tasks,
        sources,
        system_prompt,
        *,
        iteration,
        call_index,
    ):
        self.calls.append(
            (
                plan,
                search_results,
                extraction_results,
                tasks,
                sources,
                system_prompt,
                iteration,
                call_index,
            )
        )
        tokens = TokenUsage(
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
        )
        usage = AgentIterationUsage(
            agent="checker",
            iteration=iteration,
            call_index=call_index,
            scope_task_ids=[task.task_id for task in tasks],
            scope_source_ids=[source.source_id for source in sources],
            requested_model=self.model_name,
            resolved_model=self.model_name,
            service_tier="default",
            tokens=tokens,
            cost_estimate=estimate_standard_token_cost(
                self.model_name,
                tokens,
                service_tier="default",
            ),
        )
        return CheckerGeneration(
            draft=CheckerDraft(
                decisions=[
                    CheckerClaimDecisionDraft(
                        claim_id=claim.claim_id,
                        verdict=CheckerModelVerdict.ACCEPTED,
                        semantic_fit=CheckerModelSemanticFit.DIRECT,
                        source_support=CheckerModelSourceSupport.SUFFICIENT,
                        rationale=(
                            "The value is a direct semantic match for the field."
                        ),
                    )
                    for claim in extraction_results.claims
                ]
            ),
            usage=usage,
        )


class FailingCliChecker(CliFixtureChecker):
    def generate(
        self,
        plan,
        search_results,
        extraction_results,
        tasks,
        sources,
        system_prompt,
        *,
        iteration,
        call_index,
    ):
        del plan, search_results, extraction_results, system_prompt
        tokens = TokenUsage(
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
        )
        usage = AgentIterationUsage(
            agent="checker",
            iteration=iteration,
            call_index=call_index,
            scope_task_ids=[task.task_id for task in tasks],
            scope_source_ids=[source.source_id for source in sources],
            requested_model=self.model_name,
            resolved_model=self.model_name,
            service_tier="default",
            tokens=tokens,
            cost_estimate=estimate_standard_token_cost(
                self.model_name,
                tokens,
                service_tier="default",
            ),
        )
        raise CheckerProviderError(
            "Paid Checker response was unusable.",
            code="missing_structured_output",
            usage=usage,
        )


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
    def create_extractor_cli_fixture(self, output_directory):
        plan = PlannerAgent(load_question_catalog()).create_plan(
            PlannerInput(
                brand_name="Example",
                target_country="PL",
                depth="catalog",
            )
        )
        plan_path = save_research_plan(plan, output_directory)
        loaded_plan, plan_sha256 = load_research_plan(plan_path)
        search_results = SearcherAgent(CliFixtureSearcher()).create_search_results(
            loaded_plan,
            plan_sha256=plan_sha256,
            plan_reference=str(plan_path.resolve()),
            iteration=1,
            task_limit=1,
            max_search_calls=1,
            min_queries_per_task=1,
        )
        sources_path = save_search_results(search_results, plan_path)
        return plan_path, sources_path

    def create_checker_cli_fixture(self, output_directory):
        plan_path, sources_path = self.create_extractor_cli_fixture(
            output_directory
        )
        stdout = StringIO()
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
                "datacollector.cli.OpenAIExtractorClient",
                return_value=CliFixtureExtractor(),
            ),
            patch(
                "datacollector.cli.DocumentFetcher",
                return_value=RecordingDocumentFetcher(),
            ),
            redirect_stdout(stdout),
        ):
            self.assertEqual(
                main(
                    [
                        "extract",
                        "--sources",
                        str(sources_path),
                        "--limit-sources",
                        "1",
                    ]
                ),
                0,
            )
        extraction_path = Path(
            json.loads(stdout.getvalue())["extractions_path"]
        )
        return plan_path, sources_path, extraction_path

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

    def test_free_extract_creates_immutable_artifact_without_openai_usage(self):
        with TemporaryDirectory() as temporary_directory:
            _, sources_path = self.create_extractor_cli_fixture(
                temporary_directory
            )
            fetcher = RecordingDocumentFetcher()
            stdout = StringIO()
            with (
                patch(
                    "datacollector.cli.DocumentFetcher",
                    return_value=fetcher,
                ),
                patch.object(
                    OpenAISettings,
                    "from_env",
                    side_effect=AssertionError(
                        "Free Extractor must not load OpenAI settings."
                    ),
                ),
                redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "extract",
                        "--sources",
                        str(sources_path),
                        "--free",
                        "--limit-sources",
                        "1",
                    ]
                )

            summary = json.loads(stdout.getvalue())
            extraction_path = Path(summary["extractions_path"])
            artifact = json.loads(extraction_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(extraction_path.name, "extractions-free.json")
            self.assertEqual(extraction_path.parent, sources_path.parent)
            self.assertEqual(summary["generated_by"], "deterministic")
            self.assertTrue(summary["network_executed"])
            self.assertFalse(summary["provider_executed"])
            self.assertGreater(summary["evidence_passages"], 0)
            self.assertEqual(summary["citations"], 0)
            self.assertEqual(summary["raw_claims"], 0)
            self.assertEqual(summary["agent_usage"], [])
            self.assertEqual(summary["usage_totals"]["total_tokens"], 0)
            self.assertEqual(
                summary["usage_totals"]["estimated_cost_usd"],
                "0",
            )
            self.assertEqual(len(fetcher.calls), 1)
            self.assertTrue(artifact["network_executed"])
            self.assertEqual(artifact["agent_usage"], [])
            self.assertEqual(artifact["claims"], [])
            self.assertEqual(artifact["citations"], [])
            document = artifact["documents"][0]
            self.assertIsNotNone(document["content_path"])
            raw_path = extraction_path.parent / document["content_path"]
            raw_content = raw_path.read_bytes()
            self.assertEqual(len(raw_content), document["content_bytes"])
            self.assertEqual(
                hashlib.sha256(raw_content).hexdigest(),
                document["content_sha256"],
            )

            stderr = StringIO()
            with (
                patch(
                    "datacollector.cli.DocumentFetcher",
                    return_value=RecordingDocumentFetcher(),
                ),
                redirect_stderr(stderr),
            ):
                duplicate_exit_code = main(
                    [
                        "extract",
                        "--sources",
                        str(sources_path),
                        "--free",
                        "--limit-sources",
                        "1",
                    ]
                )

            self.assertEqual(duplicate_exit_code, 2)
            self.assertIn("will not be overwritten", stderr.getvalue())
            self.assertFalse(
                (sources_path.parent / ".extractions-free.json.lock").exists()
            )

            raw_path.write_bytes(b"x" * len(raw_content))
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                load_extraction_results(extraction_path)

    def test_paid_extract_reuses_free_documents_and_writes_grounded_claim(self):
        with TemporaryDirectory() as temporary_directory:
            _, sources_path = self.create_extractor_cli_fixture(
                temporary_directory
            )
            free_fetcher = RecordingDocumentFetcher()
            with (
                patch(
                    "datacollector.cli.DocumentFetcher",
                    return_value=free_fetcher,
                ),
                redirect_stdout(StringIO()),
            ):
                self.assertEqual(
                    main(
                        [
                            "extract",
                            "--sources",
                            str(sources_path),
                            "--free",
                            "--limit-sources",
                            "1",
                        ]
                    ),
                    0,
                )
            self.assertEqual(len(free_fetcher.calls), 1)

            paid_fetcher = RecordingDocumentFetcher()
            extractor = CliFixtureExtractor()
            stdout = StringIO()
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
                    "datacollector.cli.OpenAIExtractorClient",
                    return_value=extractor,
                ),
                patch(
                    "datacollector.cli.DocumentFetcher",
                    return_value=paid_fetcher,
                ),
                redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "extract",
                        "--sources",
                        str(sources_path),
                        "--limit-sources",
                        "1",
                    ]
                )

            summary = json.loads(stdout.getvalue())
            extraction_path = Path(summary["extractions_path"])
            artifact = json.loads(extraction_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(extraction_path.name, "extractions.json")
            self.assertEqual(summary["generated_by"], "openai")
            self.assertFalse(summary["network_executed"])
            self.assertTrue(summary["provider_executed"])
            self.assertEqual(summary["reused_free_documents"], 1)
            self.assertEqual(summary["raw_claims"], 1)
            self.assertEqual(len(summary["agent_usage"]), 1)
            self.assertEqual(summary["agent_usage"][0]["agent"], "extractor")
            self.assertEqual(summary["usage_totals"]["total_tokens"], 120)
            self.assertEqual(
                summary["usage_totals"]["estimated_cost_usd"],
                "0.00055000",
            )
            self.assertEqual(paid_fetcher.calls, [])
            self.assertEqual(len(extractor.calls), 1)
            self.assertEqual(artifact["generated_by"], "openai")
            self.assertFalse(artifact["network_executed"])
            self.assertEqual(len(artifact["agent_usage"]), 1)
            self.assertEqual(len(artifact["claims"]), 1)
            self.assertEqual(
                artifact["claims"][0]["value_text"],
                CLI_DOCUMENT_VALUE,
            )
            self.assertEqual(
                artifact["claims"][0]["verification_status"],
                "unverified",
            )
            self.assertEqual(
                artifact["citations"][0]["quote"],
                CLI_DOCUMENT_VALUE,
            )

    def test_paid_extract_saves_usage_when_final_artifact_write_fails(self):
        with TemporaryDirectory() as temporary_directory:
            _, sources_path = self.create_extractor_cli_fixture(
                temporary_directory
            )
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
                    "datacollector.cli.OpenAIExtractorClient",
                    return_value=CliFixtureExtractor(),
                ),
                patch(
                    "datacollector.cli.DocumentFetcher",
                    return_value=RecordingDocumentFetcher(),
                ),
                patch(
                    "datacollector.cli.save_extraction_results",
                    side_effect=OSError("fixture disk failure"),
                ),
                redirect_stderr(StringIO()),
            ):
                exit_code = main(
                    [
                        "extract",
                        "--sources",
                        str(sources_path),
                        "--limit-sources",
                        "1",
                    ]
                )

            failures = list((sources_path.parent / "attempts").glob("*.json"))
            self.assertEqual(exit_code, 2)
            self.assertEqual(len(failures), 1)
            failure = json.loads(failures[0].read_text(encoding="utf-8"))
            self.assertEqual(failure["error_code"], "artifact_write_failed")
            self.assertIsNotNone(failure["usage"])
            self.assertEqual(len(failure["scope_source_ids"]), 1)
            self.assertFalse((sources_path.parent / "extractions.json").exists())
            self.assertFalse(
                (sources_path.parent / ".extractions.json.lock").exists()
            )

    def test_paid_extract_saves_unknown_usage_attempt_on_fatal_error(self):
        with TemporaryDirectory() as temporary_directory:
            _, sources_path = self.create_extractor_cli_fixture(
                temporary_directory
            )
            sources_payload = json.loads(sources_path.read_text(encoding="utf-8"))
            source_id = sources_payload["sources"][0]["source_id"]
            task_id = sources_payload["selected_task_ids"][0]
            fatal_error = ExtractorProviderError(
                "Fixture fatal post-processing error.",
                code="postprocessing_error",
                failed_attempts=[
                    ExtractionAttemptFailure(
                        call_index=1,
                        source_id=source_id,
                        scope_task_ids=[task_id],
                        error_code="usage_unavailable",
                        usage_recorded=False,
                        token_usage_unknown=True,
                    )
                ],
            )
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
                    "datacollector.cli.OpenAIExtractorClient",
                    return_value=CliFixtureExtractor(),
                ),
                patch(
                    "datacollector.cli.ExtractorAgent.create_extraction_results",
                    side_effect=fatal_error,
                ),
                redirect_stderr(StringIO()),
            ):
                exit_code = main(
                    [
                        "extract",
                        "--sources",
                        str(sources_path),
                        "--limit-sources",
                        "1",
                    ]
                )

            failures = list((sources_path.parent / "attempts").glob("*.json"))
            self.assertEqual(exit_code, 2)
            self.assertEqual(len(failures), 1)
            failure = json.loads(failures[0].read_text(encoding="utf-8"))
            self.assertIsNone(failure["usage"])
            self.assertTrue(failure["token_usage_unknown"])
            self.assertEqual(failure["scope_source_ids"], [source_id])

    def test_free_check_uses_extractor_lineage_without_openai(self):
        with TemporaryDirectory() as temporary_directory:
            plan_path, sources_path, extraction_path = (
                self.create_checker_cli_fixture(temporary_directory)
            )
            stdout = StringIO()
            with (
                patch.object(
                    OpenAISettings,
                    "from_env",
                    side_effect=AssertionError(
                        "Free Checker must not load OpenAI settings."
                    ),
                ),
                redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "check",
                        "--extractions",
                        str(extraction_path),
                        "--free",
                    ]
                )

            summary = json.loads(stdout.getvalue())
            check_path = Path(summary["check_path"])
            results, check_sha256 = load_checker_results(check_path)
            self.assertEqual(exit_code, 0)
            self.assertEqual(check_path.name, "check-free.json")
            self.assertEqual(check_path.parent, extraction_path.parent)
            self.assertEqual(summary["generated_by"], "deterministic")
            self.assertFalse(summary["provider_executed"])
            self.assertEqual(summary["claim_verdicts"], {"not_reviewed": 1})
            self.assertIn("critical_missing_fields_count", summary)
            self.assertIn("unevaluated_critical_fields_count", summary)
            self.assertNotIn("unevaluated_critical_fields", summary)
            self.assertFalse(summary["passed"])
            self.assertEqual(
                summary["recommended_next_action"],
                "run_paid_checker",
            )
            self.assertEqual(summary["agent_usage"], [])
            self.assertEqual(summary["usage_totals"]["total_tokens"], 0)
            self.assertEqual(
                summary["usage_totals"]["estimated_cost_usd"],
                "0",
            )
            self.assertEqual(results.plan_reference, str(plan_path.resolve()))
            self.assertEqual(
                results.search_reference,
                str(sources_path.resolve()),
            )
            self.assertEqual(
                results.extraction_reference,
                str(extraction_path.resolve()),
            )
            self.assertRegex(check_sha256, r"^[a-f0-9]{64}$")

            stderr = StringIO()
            with redirect_stderr(stderr):
                duplicate_exit_code = main(
                    [
                        "check",
                        "--extractions",
                        str(extraction_path),
                        "--free",
                    ]
                )
            self.assertEqual(duplicate_exit_code, 2)
            self.assertIn("will not be overwritten", stderr.getvalue())
            self.assertFalse(
                (extraction_path.parent / ".check-free.json.lock").exists()
            )

    def test_paid_check_writes_semantic_decision_and_usage(self):
        with TemporaryDirectory() as temporary_directory:
            _, _, extraction_path = self.create_checker_cli_fixture(
                temporary_directory
            )
            checker = CliFixtureChecker()
            stdout = StringIO()
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
                    "datacollector.cli.OpenAICheckerClient",
                    return_value=checker,
                ),
                redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "check",
                        "--extractions",
                        str(extraction_path),
                    ]
                )

            summary = json.loads(stdout.getvalue())
            check_path = Path(summary["check_path"])
            artifact = json.loads(check_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(check_path.name, "check.json")
            self.assertEqual(summary["generated_by"], "openai")
            self.assertTrue(summary["provider_executed"])
            self.assertEqual(summary["selected_claims"], 1)
            self.assertEqual(summary["claim_verdicts"], {"accepted": 1})
            self.assertEqual(len(summary["agent_usage"]), 1)
            self.assertEqual(summary["agent_usage"][0]["agent"], "checker")
            self.assertEqual(summary["usage_totals"]["total_tokens"], 120)
            self.assertEqual(
                summary["usage_totals"]["estimated_cost_usd"],
                "0.00055000",
            )
            self.assertEqual(len(checker.calls), 1)
            self.assertEqual(
                artifact["claim_decisions"][0]["verdict"],
                "accepted",
            )
            self.assertEqual(len(artifact["agent_usage"]), 1)

    def test_loop_stops_at_plan_repair_limit_without_provider_spend(self):
        with TemporaryDirectory() as temporary_directory:
            _, _, extraction_path = self.create_checker_cli_fixture(
                temporary_directory
            )
            check_stdout = StringIO()
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
                    "datacollector.cli.OpenAICheckerClient",
                    return_value=CliFixtureChecker(),
                ),
                redirect_stdout(check_stdout),
            ):
                self.assertEqual(
                    main(["check", "--extractions", str(extraction_path)]),
                    0,
                )
            check_path = Path(json.loads(check_stdout.getvalue())["check_path"])

            loop_stdout = StringIO()
            with (
                patch(
                    "datacollector.cli._consecutive_gap_repair_rounds",
                    return_value=99,
                ),
                redirect_stdout(loop_stdout),
            ):
                exit_code = main(
                    [
                        "loop",
                        "--check",
                        str(check_path),
                        "--max-rounds",
                        "1",
                        "--skip-normalize",
                    ]
                )

            summary = json.loads(loop_stdout.getvalue())
            artifact = json.loads(
                Path(summary["loop_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(summary["rounds_completed"], 0)
            self.assertEqual(summary["stop_reason"], "plan_repair_limit")
            self.assertEqual(summary["usage_totals"]["total_tokens"], 0)
            self.assertEqual(summary["usage_totals"]["estimated_cost_usd"], "0")
            self.assertEqual(artifact["policy"]["allow_plan_repair_limit"], False)
            self.assertEqual(artifact["recommended_next_action"], "inspect_gaps")

    def test_loop_upgrades_free_checker_and_records_incremental_cost(self):
        with TemporaryDirectory() as temporary_directory:
            _, _, extraction_path = self.create_checker_cli_fixture(
                temporary_directory
            )
            free_stdout = StringIO()
            with redirect_stdout(free_stdout):
                self.assertEqual(
                    main(
                        [
                            "check",
                            "--extractions",
                            str(extraction_path),
                            "--free",
                        ]
                    ),
                    0,
                )
            free_check_path = Path(
                json.loads(free_stdout.getvalue())["check_path"]
            )

            loop_stdout = StringIO()
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
                    "datacollector.cli.OpenAICheckerClient",
                    return_value=CliFixtureChecker(),
                ),
                redirect_stdout(loop_stdout),
            ):
                exit_code = main(
                    [
                        "loop",
                        "--check",
                        str(free_check_path),
                        "--max-rounds",
                        "1",
                        "--skip-normalize",
                    ]
                )

            summary = json.loads(loop_stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(summary["rounds_completed"], 1)
            self.assertEqual(summary["rounds"][0]["action"], "run_paid_checker")
            self.assertEqual(summary["stop_reason"], "max_rounds")
            self.assertEqual(summary["usage_totals"]["total_tokens"], 120)
            self.assertEqual(
                summary["usage_totals"]["estimated_cost_usd"],
                "0.00055000",
            )
            self.assertTrue(Path(summary["loop_path"]).is_file())

    def test_paid_check_preserves_failed_attempt_in_final_artifact(self):
        with TemporaryDirectory() as temporary_directory:
            _, _, extraction_path = self.create_checker_cli_fixture(
                temporary_directory
            )
            stdout = StringIO()
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
                    "datacollector.cli.OpenAICheckerClient",
                    return_value=FailingCliChecker(),
                ),
                redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "check",
                        "--extractions",
                        str(extraction_path),
                    ]
                )

            summary = json.loads(stdout.getvalue())
            artifact = json.loads(
                Path(summary["check_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(summary["claim_verdicts"], {"not_reviewed": 1})
            self.assertEqual(
                summary["recommended_next_action"],
                "retry_checker",
            )
            self.assertEqual(summary["usage_totals"]["total_tokens"], 120)
            self.assertEqual(len(summary["failed_attempts"]), 1)
            self.assertEqual(
                artifact["failed_attempts"][0]["error_code"],
                "missing_structured_output",
            )
            self.assertTrue(
                artifact["failed_attempts"][0]["usage_recorded"]
            )

    def test_paid_check_saves_usage_when_final_artifact_write_fails(self):
        with TemporaryDirectory() as temporary_directory:
            _, _, extraction_path = self.create_checker_cli_fixture(
                temporary_directory
            )
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
                    "datacollector.cli.OpenAICheckerClient",
                    return_value=CliFixtureChecker(),
                ),
                patch(
                    "datacollector.cli.save_checker_results",
                    side_effect=OSError("fixture disk failure"),
                ),
                redirect_stderr(StringIO()),
            ):
                exit_code = main(
                    [
                        "check",
                        "--extractions",
                        str(extraction_path),
                    ]
                )

            failures = list(
                (extraction_path.parent / "attempts").glob("checker-*.json")
            )
            self.assertEqual(exit_code, 2)
            self.assertEqual(len(failures), 1)
            failure = json.loads(failures[0].read_text(encoding="utf-8"))
            self.assertEqual(failure["error_code"], "artifact_write_failed")
            self.assertEqual(failure["agent"], "checker")
            self.assertIsNotNone(failure["usage"])
            self.assertFalse((extraction_path.parent / "check.json").exists())
            self.assertFalse(
                (extraction_path.parent / ".check.json.lock").exists()
            )
