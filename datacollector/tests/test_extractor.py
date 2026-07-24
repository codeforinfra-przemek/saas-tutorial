import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from unittest import TestCase
from uuid import uuid4

from datacollector.agents.extractor import (
    ExtractorAgent,
    ExtractorValidationError,
    _build_passages,
    _select_sources,
)
from datacollector.agents.planner import PlannerAgent
from datacollector.catalog import load_question_catalog
from datacollector.documents import FetchedDocument, FetchStatus
from datacollector.llm.pricing import build_web_search_tool_usage
from datacollector.llm.protocol import (
    ExtractorGeneration,
    ExtractorProviderError,
)
from datacollector.schemas import (
    AgentIterationUsage,
    DocumentParseStatus,
    DocumentRetrievalStatus,
    ExtractionConfidence,
    ExtractionTaskStatus,
    ExtractorClaimDraft,
    ExtractorDraft,
    FieldExtractionStatus,
    PlannerInput,
    SearchAction,
    SearchLimits,
    SearchQueryCoverage,
    SearchResults,
    SearchSource,
    SearchSourceOrigin,
    SearchTaskResult,
    SearchTaskStatus,
    SourceDocument,
    SourceType,
    TokenUsage,
)


NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
PLAN_SHA256 = "a" * 64
SEARCH_SHA256 = "b" * 64
DOCUMENT_TEXT = (
    "Example Polska sp. z o.o. is the official franchise operator. "
    "Its registry number is 1234567890. The company offers training and support."
)
EXACT_QUOTE = "Example Polska sp. z o.o. is the official franchise operator."
EXACT_VALUE = "Example Polska sp. z o.o."


def make_usage(*, iteration, call_index, task_ids, source_id):
    return AgentIterationUsage(
        agent="extractor",
        iteration=iteration,
        call_index=call_index,
        scope_task_ids=task_ids,
        scope_source_ids=[source_id],
        requested_model="fake-extractor-model",
        resolved_model="fake-extractor-model",
        response_id=f"resp-extractor-{call_index}",
        tokens=TokenUsage(
            input_tokens=100,
            output_tokens=20,
            reasoning_tokens=5,
            total_tokens=120,
        ),
    )


class FakeFetcher:
    def __init__(self, documents):
        self.documents = documents
        self.calls = []

    def fetch(self, url, *, source_id=""):
        self.calls.append((url, source_id))
        value = self.documents[source_id]
        if isinstance(value, Exception):
            raise value
        return value


class NeverFetcher:
    def __init__(self):
        self.calls = []

    def fetch(self, url, *, source_id=""):
        self.calls.append((url, source_id))
        raise AssertionError("A valid cached document must prevent network fetching.")


class FakeExtractorLLM:
    model_name = "fake-extractor-model"

    def __init__(self, handler):
        self.handler = handler
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
            {
                "source": source,
                "document": document,
                "tasks": tasks,
                "passages": passages,
                "iteration": iteration,
                "call_index": call_index,
            }
        )
        return self.handler(
            plan=plan,
            source=source,
            document=document,
            tasks=tasks,
            passages=passages,
            iteration=iteration,
            call_index=call_index,
        )


class ExtractorAgentTests(TestCase):
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

    def _source(self, number=1, *, source_type=SourceType.OFFICIAL):
        source_id = f"source-{number:016x}"
        url = f"https://example{number}.com/franchise"
        return SearchSource(
            source_id=source_id,
            url=url,
            canonical_url=url,
            title=f"Official source {number}",
            source_type=source_type,
            origin=SearchSourceOrigin.OPENAI_WEB_SEARCH,
            provider_observed=True,
            task_ids=[self.task.task_id],
            observed_in_action_ids=["action-fixture"],
            discovered_via_queries=[],
            relevance_note="Fixture source mapped to one plan task.",
            discovered_at=NOW,
        )

    def _search_results(self, sources):
        query = self.task.search_queries[0]
        source_urls = [source.canonical_url for source in sources]
        source_ids = [source.source_id for source in sources]
        return SearchResults(
            search_id=str(uuid4()),
            plan_run_id=self.plan.run_id,
            plan_sha256=PLAN_SHA256,
            plan_reference="/fixtures/plan.json",
            created_at=NOW,
            iteration=1,
            generated_by="openai",
            model="fake-searcher-model",
            brand_name=self.plan.planner_input.brand_name,
            target_country=self.plan.planner_input.target_country,
            depth=self.plan.planner_input.depth,
            search_executed=True,
            limits=SearchLimits(
                max_search_calls=1,
                task_limit=1,
                min_queries_per_task=1,
            ),
            selected_task_ids=[self.task.task_id],
            unselected_task_ids=[
                task.task_id
                for task in self.plan.tasks
                if task.task_id != self.task.task_id
            ],
            actions=[
                SearchAction(
                    action_id="action-fixture",
                    call_index=1,
                    scope_task_ids=[self.task.task_id],
                    action_type="search",
                    status="completed",
                    queries=[query],
                    source_urls=source_urls,
                )
            ],
            sources=sources,
            task_results=[
                SearchTaskResult(
                    task_id=self.task.task_id,
                    catalog_question_id=self.task.catalog_question_id,
                    status=SearchTaskStatus.SOURCES_FOUND,
                    planned_queries=[query],
                    attempted_queries=[query],
                    planned_queries_attempted=[query],
                    derived_queries_attempted=[],
                    query_coverage=SearchQueryCoverage.COMPLETE,
                    minimum_query_attempts=1,
                    minimum_sources=1,
                    action_ids=["action-fixture"],
                    source_ids=source_ids,
                    coverage_gaps=[],
                    unresolved_targets=[],
                )
            ],
            warnings=[],
            compliance_rules=self.plan.compliance_rules,
            agent_usage=[
                AgentIterationUsage(
                    agent="searcher",
                    iteration=1,
                    call_index=1,
                    scope_task_ids=[self.task.task_id],
                    requested_model="fake-searcher-model",
                    resolved_model="fake-searcher-model",
                    tokens=TokenUsage(
                        input_tokens=50,
                        output_tokens=10,
                        total_tokens=60,
                    ),
                    tool_usage=[build_web_search_tool_usage({"search": 1})],
                )
            ],
        )

    def test_automatic_source_selection_prioritizes_official_first_seed(self):
        weak = self._source(1, source_type=SourceType.BLOG)
        seed = self._source(2, source_type=SourceType.UNKNOWN).model_copy(
            update={
                "url": "https://example.com/franczyza/",
                "canonical_url": "https://example.com/franczyza/",
                "origin": SearchSourceOrigin.PLAN_SEED,
                "provider_observed": False,
                "observed_in_action_ids": [],
            }
        )
        selected = _select_sources(
            self._search_results([weak, seed]),
            requested_source_ids=[],
            source_limit=1,
        )
        self.assertEqual([source.source_id for source in selected], [seed.source_id])

    def test_explicit_source_selection_is_not_reordered(self):
        first = self._source(1, source_type=SourceType.BLOG)
        seed = self._source(2, source_type=SourceType.UNKNOWN).model_copy(
            update={
                "origin": SearchSourceOrigin.PLAN_SEED,
                "provider_observed": False,
                "observed_in_action_ids": [],
            }
        )
        selected = _select_sources(
            self._search_results([first, seed]),
            requested_source_ids=[first.source_id, seed.source_id],
            source_limit=2,
        )
        self.assertEqual(
            [source.source_id for source in selected],
            [first.source_id, seed.source_id],
        )

    @staticmethod
    def _fetched(source, *, source_id=None):
        content = DOCUMENT_TEXT.encode("utf-8")
        return FetchedDocument(
            source_id=source_id or source.source_id,
            requested_url=source.canonical_url,
            final_url=source.canonical_url,
            status=FetchStatus.FETCHED,
            fetched_at=NOW,
            http_status=200,
            media_type="text/html",
            content=content,
            text=DOCUMENT_TEXT,
            title=source.title,
            byte_count=len(content),
            content_sha256=hashlib.sha256(content).hexdigest(),
            text_sha256=hashlib.sha256(DOCUMENT_TEXT.encode()).hexdigest(),
        )

    def _run(
        self,
        sources,
        *,
        fetcher=None,
        llm=None,
        search_results=None,
        cached_documents=None,
        **kwargs,
    ):
        results = search_results or self._search_results(sources)
        if fetcher is None:
            fetcher = FakeFetcher(
                {source.source_id: self._fetched(source) for source in sources}
            )
        extraction = ExtractorAgent(fetcher, llm).create_extraction_results(
            self.plan,
            results,
            plan_sha256=PLAN_SHA256,
            search_sha256=SEARCH_SHA256,
            search_reference="/fixtures/sources.json",
            source_limit=len(sources),
            cached_documents=cached_documents,
            **kwargs,
        )
        return extraction, fetcher

    def _successful_generation(self, **context):
        source = context["source"]
        passage = context["passages"][0]
        task = next(
            task
            for task in context["tasks"]
            if task.task_id == passage.task_id
        )
        draft = ExtractorDraft(
            claims=[
                ExtractorClaimDraft(
                    task_id=task.task_id,
                    target_field=task.target_fields[0],
                    passage_id=passage.passage_id,
                    value_text=EXACT_VALUE,
                    evidence_quote=EXACT_QUOTE,
                    confidence=ExtractionConfidence.HIGH,
                )
            ],
            warnings=[],
        )
        return ExtractorGeneration(
            draft=draft,
            usage=make_usage(
                iteration=context["iteration"],
                call_index=context["call_index"],
                task_ids=[task.task_id],
                source_id=source.source_id,
            ),
            source_id=source.source_id,
        )

    def test_free_fetch_and_parse_produces_content_only_passages_without_claims(self):
        source = self._source()

        results, fetcher = self._run([source])

        self.assertEqual(fetcher.calls, [(source.canonical_url, source.source_id)])
        self.assertTrue(results.network_executed)
        self.assertFalse(results.provider_executed)
        self.assertEqual(results.generated_by, "deterministic")
        self.assertEqual(results.documents[0].retrieval_status, DocumentRetrievalStatus.FETCHED)
        self.assertEqual(results.documents[0].parse_status, DocumentParseStatus.PARSED)
        self.assertTrue(results.evidence_passages)
        self.assertEqual(results.task_results[0].status, ExtractionTaskStatus.CONTENT_ONLY)
        self.assertTrue(
            all(
                field.status == FieldExtractionStatus.NOT_PROCESSED
                for field in results.task_results[0].field_results
            )
        )
        self.assertEqual(results.citations, [])
        self.assertEqual(results.claims, [])
        self.assertEqual(results.agent_usage, [])
        self.assertEqual(results.failed_attempts, [])

    def test_paid_exact_quote_and_value_create_grounded_citation_claim_and_usage(self):
        source = self._source()
        llm = FakeExtractorLLM(self._successful_generation)

        results, _ = self._run([source], llm=llm, iteration=3)

        self.assertTrue(results.provider_executed)
        self.assertEqual(results.generated_by, "openai")
        self.assertEqual(len(results.agent_usage), 1)
        self.assertEqual(results.agent_usage[0].iteration, 3)
        self.assertEqual(len(results.citations), 1)
        self.assertEqual(len(results.claims), 1)
        citation = results.citations[0]
        claim = results.claims[0]
        document = results.documents[0]
        self.assertEqual(citation.quote, EXACT_QUOTE)
        self.assertEqual(
            document.text[citation.start_char : citation.end_char],
            EXACT_QUOTE,
        )
        self.assertEqual(citation.text_sha256, document.text_sha256)
        self.assertEqual(claim.value_text, EXACT_VALUE)
        self.assertEqual(claim.citation_ids, [citation.citation_id])
        field = next(
            item
            for item in results.task_results[0].field_results
            if item.target_field == self.task.target_fields[0]
        )
        self.assertEqual(field.status, FieldExtractionStatus.EXTRACTED)
        self.assertEqual(field.claim_ids, [claim.claim_id])

    def test_profile_extractor_payload_excludes_private_fields(self):
        original_plan, original_task = self.plan, self.task
        try:
            self.plan = PlannerAgent(load_question_catalog()).create_plan(
                PlannerInput(
                    brand_name="Example", target_country="PL", profile_id="PL:L3"
                )
            )
            self.task = next(
                task
                for task in self.plan.tasks
                if task.catalog_question_id == "fdd06.other_fees"
            )
            source = self._source()
            llm = FakeExtractorLLM(self._successful_generation)

            results, _ = self._run([source], llm=llm, iteration=3)

            supplied_task = llm.calls[0]["tasks"][0]
            self.assertIn("fees.royalty", supplied_task.target_fields)
            self.assertNotIn("fees.audit", supplied_task.target_fields)
            private_field = next(
                field
                for field in results.task_results[0].field_results
                if field.target_field == "fees.audit"
            )
            self.assertEqual(
                private_field.status, FieldExtractionStatus.NOT_PROCESSED
            )
            self.assertIn("excluded", private_field.notes)
        finally:
            self.plan, self.task = original_plan, original_task

    def test_profile_extractor_rejects_human_only_source_before_fetch(self):
        original_plan, original_task = self.plan, self.task
        try:
            self.plan = PlannerAgent(load_question_catalog()).create_plan(
                PlannerInput(
                    brand_name="Example", target_country="PL", profile_id="PL:L3"
                )
            )
            self.task = next(
                task
                for task in self.plan.tasks
                if task.catalog_question_id == "fdd22.contracts"
            )
            source = self._source()
            fetcher = FakeFetcher(
                {source.source_id: self._fetched(source)}
            )

            with self.assertRaisesRegex(
                ExtractorValidationError, "forbids automated extraction"
            ):
                self._run([source], fetcher=fetcher)

            self.assertEqual(fetcher.calls, [])
        finally:
            self.plan, self.task = original_plan, original_task

    def test_bad_quote_field_and_passage_are_rejected_but_usage_is_retained(self):
        source = self._source()

        def invalid_generation(**context):
            passage = context["passages"][0]
            drafts = [
                ExtractorClaimDraft(
                    task_id=self.task.task_id,
                    target_field=self.task.target_fields[0],
                    passage_id=passage.passage_id,
                    value_text=EXACT_VALUE,
                    evidence_quote=(
                        "Example Polska sp. z o.o. is a fabricated operator."
                    ),
                    confidence=ExtractionConfidence.LOW,
                ),
                ExtractorClaimDraft(
                    task_id=self.task.task_id,
                    target_field="unknown.target_field",
                    passage_id=passage.passage_id,
                    value_text=EXACT_VALUE,
                    evidence_quote=EXACT_QUOTE,
                    confidence=ExtractionConfidence.LOW,
                ),
                ExtractorClaimDraft(
                    task_id=self.task.task_id,
                    target_field=self.task.target_fields[0],
                    passage_id="passage-ffffffffffffffff",
                    value_text=EXACT_VALUE,
                    evidence_quote=EXACT_QUOTE,
                    confidence=ExtractionConfidence.LOW,
                ),
                ExtractorClaimDraft(
                    task_id=self.task.task_id,
                    target_field=self.task.target_fields[0],
                    passage_id=passage.passage_id,
                    value_text=EXACT_VALUE,
                    evidence_quote=EXACT_QUOTE,
                    publisher_text="Hallucinated Publisher",
                    confidence=ExtractionConfidence.LOW,
                ),
            ]
            return ExtractorGeneration(
                draft=ExtractorDraft(claims=drafts, warnings=[]),
                usage=make_usage(
                    iteration=context["iteration"],
                    call_index=context["call_index"],
                    task_ids=[self.task.task_id],
                    source_id=context["source"].source_id,
                ),
                source_id=context["source"].source_id,
            )

        results, _ = self._run(
            [source],
            llm=FakeExtractorLLM(invalid_generation),
        )

        self.assertEqual(results.claims, [])
        self.assertEqual(results.citations, [])
        self.assertEqual(len(results.agent_usage), 1)
        self.assertTrue(
            any(
                "Rejected 4 ungrounded or out-of-contract claim draft(s)" in warning
                for warning in results.warnings
            )
        )

    def test_routing_lead_content_never_reaches_paid_llm(self):
        source = self._source(source_type=SourceType.ROUTING_LEAD)

        def must_not_run(**context):
            raise AssertionError("routing_lead must not invoke semantic extraction")

        llm = FakeExtractorLLM(must_not_run)
        results, _ = self._run([source], llm=llm)

        self.assertEqual(llm.calls, [])
        self.assertEqual(results.evidence_passages, [])
        self.assertEqual(results.claims, [])
        self.assertEqual(results.agent_usage, [])
        self.assertFalse(results.provider_executed)

    def test_provider_failures_with_and_without_usage_are_both_ledgered(self):
        first = self._source(1)
        second = self._source(2)

        def failing_generation(**context):
            usage = None
            if context["call_index"] == 1:
                usage = make_usage(
                    iteration=context["iteration"],
                    call_index=context["call_index"],
                    task_ids=[self.task.task_id],
                    source_id=context["source"].source_id,
                )
            raise ExtractorProviderError(
                "Fixture failure.",
                code=(
                    "failed_with_usage"
                    if usage is not None
                    else "failed_without_usage"
                ),
                usage=usage,
                iteration=context["iteration"],
                call_index=context["call_index"],
                scope_task_ids=[self.task.task_id],
                requested_model="fake-extractor-model",
                source_id=context["source"].source_id,
            )

        results, _ = self._run(
            [first, second],
            llm=FakeExtractorLLM(failing_generation),
            max_api_calls=2,
        )

        self.assertEqual(len(results.failed_attempts), 2)
        self.assertTrue(results.provider_executed)
        with_usage, without_usage = results.failed_attempts
        self.assertTrue(with_usage.usage_recorded)
        self.assertFalse(with_usage.token_usage_unknown)
        self.assertFalse(without_usage.usage_recorded)
        self.assertTrue(without_usage.token_usage_unknown)
        self.assertEqual(len(results.agent_usage), 1)
        self.assertEqual(results.agent_usage[0].call_index, 1)

    def test_valid_cached_document_prevents_fetch_and_rebuilds_passages(self):
        source = self._source()
        search_results = self._search_results([source])
        first_results, _ = self._run(
            [source],
            search_results=search_results,
        )
        never_fetcher = NeverFetcher()

        cached_results, _ = self._run(
            [source],
            fetcher=never_fetcher,
            search_results=search_results,
            cached_documents=first_results.documents,
            cached_document_origin="the exact predecessor Extractor artifact",
        )

        self.assertEqual(never_fetcher.calls, [])
        self.assertFalse(cached_results.network_executed)
        self.assertEqual(cached_results.documents, first_results.documents)
        self.assertEqual(
            [item.passage_id for item in cached_results.evidence_passages],
            [item.passage_id for item in first_results.evidence_passages],
        )
        self.assertTrue(any("Reused 1 matching document" in item for item in cached_results.warnings))
        self.assertTrue(
            any(
                "the exact predecessor Extractor artifact" in item
                for item in cached_results.warnings
            )
        )
        self.assertFalse(
            any("prior free Extractor" in item for item in cached_results.warnings)
        )

    def test_trusted_predecessor_cache_is_not_bound_to_new_search_uuid(self):
        source = self._source()
        first_search = self._search_results([source])
        first_results, _ = self._run(
            [source],
            search_results=first_search,
        )
        next_search = first_search.model_copy(
            update={"search_id": str(uuid4())}
        )
        never_fetcher = NeverFetcher()

        cached_results, _ = self._run(
            [source],
            fetcher=never_fetcher,
            search_results=next_search,
            cached_documents=first_results.documents,
            trust_cached_document_ids=True,
            cached_document_origin="the exact validated predecessor artifact",
        )

        self.assertEqual(never_fetcher.calls, [])
        self.assertEqual(cached_results.documents, first_results.documents)
        self.assertFalse(cached_results.network_executed)

    def test_failed_free_document_is_retried_instead_of_cached_for_paid_mode(self):
        source = self._source()
        search_results = self._search_results([source])
        failed_fetch = FetchedDocument(
            source_id=source.source_id,
            requested_url=source.canonical_url,
            final_url=source.canonical_url,
            status=FetchStatus.NETWORK_ERROR,
            fetched_at=NOW,
            error_code="network_error",
        )
        free_results, _ = self._run(
            [source],
            fetcher=FakeFetcher({source.source_id: failed_fetch}),
            search_results=search_results,
        )
        retry_fetcher = FakeFetcher(
            {source.source_id: self._fetched(source)}
        )

        paid_results, _ = self._run(
            [source],
            fetcher=retry_fetcher,
            llm=FakeExtractorLLM(self._successful_generation),
            search_results=search_results,
            cached_documents=free_results.documents,
        )

        self.assertEqual(
            retry_fetcher.calls,
            [(source.canonical_url, source.source_id)],
        )
        self.assertEqual(
            paid_results.documents[0].parse_status,
            DocumentParseStatus.PARSED,
        )

    def test_terminal_free_anti_bot_result_is_reused_without_paid_refetch(self):
        source = self._source()
        search_results = self._search_results([source])
        content = b"Imperva anti-bot challenge"
        anti_bot = FetchedDocument(
            source_id=source.source_id,
            requested_url=source.canonical_url,
            final_url=source.canonical_url,
            status=FetchStatus.ANTI_BOT,
            fetched_at=NOW,
            http_status=200,
            media_type="text/html",
            content=content,
            byte_count=len(content),
            content_sha256=hashlib.sha256(content).hexdigest(),
            error_code="anti_bot_page",
        )
        free_results, _ = self._run(
            [source],
            fetcher=FakeFetcher({source.source_id: anti_bot}),
            search_results=search_results,
        )
        never_fetcher = NeverFetcher()

        paid_results, _ = self._run(
            [source],
            fetcher=never_fetcher,
            llm=FakeExtractorLLM(self._successful_generation),
            search_results=search_results,
            cached_documents=free_results.documents,
        )

        self.assertEqual(never_fetcher.calls, [])
        self.assertFalse(paid_results.network_executed)
        self.assertFalse(paid_results.provider_executed)
        self.assertEqual(paid_results.documents, free_results.documents)
        self.assertTrue(
            any(
                "Reused 1 terminal retrieval result" in warning
                for warning in paid_results.warnings
            )
        )

    def test_passage_ranking_handles_polish_inflection_and_drops_boilerplate(self):
        source = self._source()
        task = self.task.model_copy(
            update={
                "title": "Current document inventory",
                "question": "Which franchise agreements and documents are available?",
                "acceptance_criteria": "Identify each agreement and document.",
                "target_fields": [
                    "documents.inventory",
                    "franchisor.legal_name",
                ],
                "search_queries": ["Żabka umowa franczyzy PDF"],
            }
        )
        text = (
            "Żabka Menu\n\n"
            "Wyrażam zgodę na przetwarzanie przez Żabka Polska sp. z o.o. "
            "moich danych osobowych i przesyłanie newslettera.\n\n"
            "Franczyzobiorca podpisuje ze spółką umowę współpracy dotyczącą "
            "prowadzenia sklepu Żabka. Dokument umowy określa zasady franczyzy."
        )
        content = text.encode()
        document = SourceDocument(
            document_id="document-1111111111111111",
            source_id=source.source_id,
            canonical_url=source.canonical_url,
            final_url=source.canonical_url,
            task_ids=[task.task_id],
            retrieval_status=DocumentRetrievalStatus.FETCHED,
            parse_status=DocumentParseStatus.PARSED,
            collected_at=NOW,
            http_status=200,
            media_type="text/html",
            content_bytes=len(content),
            content_sha256=hashlib.sha256(content).hexdigest(),
            text=text,
            text_chars=len(text),
            processed_chars=len(text),
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            parser="html.parser",
        )

        passages = _build_passages(
            document,
            [task],
            max_passages_per_task=6,
        )

        self.assertEqual(len(passages), 1)
        self.assertIn("umowę współpracy", passages[0].text)
        self.assertIn("umow", passages[0].matched_terms)
        self.assertNotIn("Wyrażam zgodę", passages[0].text)
        self.assertNotIn("Menu", passages[0].text)

    def test_passage_ranking_keeps_short_table_labels_with_their_values(self):
        source = self._source()
        task = self.task.model_copy(
            update={
                "target_fields": ["franchisor.parent_entities"],
                "search_queries": ["Example akcjonariusze struktura właścicielska"],
            }
        )
        text = (
            "Akcjonariusz\n\n"
            "Heket Topco S.a r.l.\n\n"
            "377 364 050\n\n"
            "37,624%\n\n"
            "Pozostali\n\n"
            "62,376%\n\n"
            "Polityka prywatności\n\n"
            "Zarządzaj cookies"
        )
        content = text.encode()
        document = SourceDocument(
            document_id="document-2222222222222222",
            source_id=source.source_id,
            canonical_url=source.canonical_url,
            final_url=source.canonical_url,
            task_ids=[task.task_id],
            retrieval_status=DocumentRetrievalStatus.FETCHED,
            parse_status=DocumentParseStatus.PARSED,
            collected_at=NOW,
            http_status=200,
            media_type="text/html",
            content_bytes=len(content),
            content_sha256=hashlib.sha256(content).hexdigest(),
            text=text,
            text_chars=len(text),
            processed_chars=len(text),
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            parser="html.parser",
        )

        passages = _build_passages(
            document,
            [task],
            max_passages_per_task=6,
        )

        table_passage = next(
            passage for passage in passages if "Heket Topco" in passage.text
        )
        self.assertIn("Akcjonariusz", table_passage.text)
        self.assertIn("37,624%", table_passage.text)
        self.assertNotIn("Polityka prywatności", table_passage.text)

    def test_partial_document_and_search_backlog_remain_explicit(self):
        source = self._source()
        search_results = self._search_results([source])
        search_results = search_results.model_copy(
            update={
                "task_results": [
                    search_results.task_results[0].model_copy(
                        update={
                            "unresolved_targets": ["Official KRS extract"],
                        }
                    )
                ]
            }
        )
        fetched = replace(self._fetched(source), status=FetchStatus.PARTIAL)

        results, _ = self._run(
            [source],
            fetcher=FakeFetcher({source.source_id: fetched}),
            search_results=search_results,
        )

        task_result = results.task_results[0]
        self.assertEqual(
            task_result.inherited_search_unresolved_targets,
            ["Official KRS extract"],
        )
        self.assertIn(
            f"document_text_partial:{source.source_id}",
            task_result.coverage_gaps,
        )

    def test_pdf_selection_finds_relevant_later_page_and_preserves_locator(self):
        source = self._source()
        pages = (
            "Generic corporate boilerplate. " * 45,
            "Unrelated definitions and table of contents. " * 8,
            (
                "The franchisor legal name is Example Polska sp. z o.o. and "
                "the official franchise website is example.test/franchise."
            ),
        )
        content = b"%PDF-1.7 fixture bytes"
        fetched = FetchedDocument(
            source_id=source.source_id,
            requested_url=source.canonical_url,
            final_url=source.canonical_url,
            status=FetchStatus.FETCHED,
            fetched_at=NOW,
            http_status=200,
            media_type="application/pdf",
            content=content,
            text="\n\f\n".join(pages),
            page_text=pages,
            page_count=3,
            parsed_pages=3,
            byte_count=len(content),
            content_sha256=hashlib.sha256(content).hexdigest(),
        )

        results, _ = self._run(
            [source],
            fetcher=FakeFetcher({source.source_id: fetched}),
            max_document_chars=1_000,
        )

        document = results.documents[0]
        self.assertEqual(document.page_count, 3)
        self.assertEqual(document.parsed_pages, 3)
        self.assertIn(3, document.selected_page_numbers)
        self.assertIn("franchisor legal name", document.text)
        relevant_passage = next(
            passage
            for passage in results.evidence_passages
            if "franchisor legal name" in passage.text
        )
        self.assertEqual(relevant_passage.locator, "page:0003")

    def test_paid_api_call_cap_leaves_remaining_source_explicitly_unprocessed(self):
        first = self._source(1)
        second = self._source(2)
        llm = FakeExtractorLLM(self._successful_generation)

        results, _ = self._run(
            [first, second],
            llm=llm,
            max_api_calls=1,
        )

        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(len(results.agent_usage), 1)
        self.assertTrue(
            any("API-call cap reached" in warning for warning in results.warnings)
        )
        self.assertIn(
            "semantic_sources_processed:1/2",
            results.task_results[0].coverage_gaps,
        )

    def test_rejects_invalid_plan_lineage_unknown_source_and_unknown_task_mapping(self):
        source = self._source()
        search_results = self._search_results([source])
        fetcher = FakeFetcher({source.source_id: self._fetched(source)})
        agent = ExtractorAgent(fetcher)

        with self.assertRaises(ExtractorValidationError):
            agent.create_extraction_results(
                self.plan,
                search_results,
                plan_sha256="c" * 64,
                search_sha256=SEARCH_SHA256,
                search_reference="/fixtures/sources.json",
            )
        with self.assertRaises(ExtractorValidationError):
            agent.create_extraction_results(
                self.plan,
                search_results,
                plan_sha256=PLAN_SHA256,
                search_sha256=SEARCH_SHA256,
                search_reference="/fixtures/sources.json",
                requested_source_ids=["source-ffffffffffffffff"],
            )

        unknown_task_id = "task-absent-from-plan"
        invalid_source = source.model_copy(
            update={"task_ids": [unknown_task_id]}
        )
        invalid_task_result = search_results.task_results[0].model_copy(
            update={"task_id": unknown_task_id}
        )
        invalid_search = search_results.model_copy(
            update={
                "selected_task_ids": [unknown_task_id],
                "sources": [invalid_source],
                "task_results": [invalid_task_result],
            }
        )
        with self.assertRaises(ExtractorValidationError):
            agent.create_extraction_results(
                self.plan,
                invalid_search,
                plan_sha256=PLAN_SHA256,
                search_sha256=SEARCH_SHA256,
                search_reference="/fixtures/sources.json",
            )
        self.assertEqual(fetcher.calls, [])

    def test_fetcher_result_must_match_requested_source_id(self):
        source = self._source()
        mismatched = self._fetched(
            source,
            source_id="source-ffffffffffffffff",
        )
        fetcher = FakeFetcher({source.source_id: mismatched})

        results, _ = self._run([source], fetcher=fetcher)

        self.assertNotEqual(
            results.documents[0].parse_status,
            DocumentParseStatus.PARSED,
            "Extractor accepted FetchedDocument content attributed to another source.",
        )
        self.assertEqual(results.evidence_passages, [])

    def test_provider_generation_must_match_requested_source_id(self):
        source = self._source()

        def mismatched_generation(**context):
            generation = self._successful_generation(**context)
            return replace(
                generation,
                source_id="source-ffffffffffffffff",
            )

        results, _ = self._run(
            [source],
            llm=FakeExtractorLLM(mismatched_generation),
        )

        self.assertEqual(
            results.claims,
            [],
            "Extractor accepted provider output attributed to another source.",
        )
        self.assertEqual(len(results.failed_attempts), 1)
