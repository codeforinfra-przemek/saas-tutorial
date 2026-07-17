from unittest import TestCase

from pydantic import ValidationError

from datacollector.catalog import load_question_catalog, select_questions
from datacollector.schemas import (
    CatalogQuestion,
    PlannerInput,
    Requirement,
    ResearchDepth,
)


class QuestionCatalogTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_question_catalog()

    def test_catalog_has_unique_question_ids_and_all_fdd_items(self):
        questions = self.catalog.all_questions()
        ids = [question.id for question in questions]
        fdd_items = {item for question in questions for item in question.fdd_items}

        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(fdd_items, set(range(1, 24)))

    def test_every_critical_question_has_evidence_acceptance_criteria(self):
        critical = [
            question
            for question in self.catalog.all_questions()
            if question.requirement == Requirement.CRITICAL
        ]

        self.assertTrue(critical)
        for question in critical:
            with self.subTest(question=question.id):
                self.assertGreaterEqual(question.evidence.min_sources, 1)
                self.assertGreater(len(question.evidence.acceptance_criteria), 20)
                self.assertTrue(question.target_fields)

    def test_us_process_questions_are_selected_for_us_and_territories(self):
        pl_questions = select_questions(
            self.catalog,
            PlannerInput(
                brand_name="Test",
                target_country="PL",
                depth=ResearchDepth.DUE_DILIGENCE,
            ),
        )
        us_questions = select_questions(
            self.catalog,
            PlannerInput(
                brand_name="Test",
                target_country="US",
                depth=ResearchDepth.DUE_DILIGENCE,
            ),
        )

        pl_ids = {question.id for _, question in pl_questions}
        us_ids = {question.id for _, question in us_questions}
        self.assertNotIn("us.state_overlay", pl_ids)
        self.assertIn("us.state_overlay", us_ids)
        self.assertGreater(len(us_ids), len(pl_ids))

        for territory in ("PR", "GU", "VI", "AS", "MP", "UM"):
            territory_questions = select_questions(
                self.catalog,
                PlannerInput(
                    brand_name="Test",
                    target_country=territory,
                    depth=ResearchDepth.DUE_DILIGENCE,
                ),
            )
            territory_ids = {question.id for _, question in territory_questions}
            with self.subTest(territory=territory):
                self.assertIn("us.definition_and_exemptions", territory_ids)
                self.assertIn("us.delivery_and_updates", territory_ids)

    def test_depths_are_cumulative(self):
        counts = []
        for depth in ResearchDepth:
            selected = select_questions(
                self.catalog,
                PlannerInput(brand_name="Test", target_country="PL", depth=depth),
            )
            counts.append(len(selected))

        self.assertEqual(counts, sorted(counts))
        self.assertGreater(counts[-1], counts[0])

    def test_every_selected_question_keeps_all_dependencies(self):
        for country in ("PL", "US"):
            for depth in ResearchDepth:
                selected = select_questions(
                    self.catalog,
                    PlannerInput(
                        brand_name="Test",
                        target_country=country,
                        depth=depth,
                    ),
                )
                selected_ids = {question.id for _, question in selected}
                for _, question in selected:
                    with self.subTest(
                        country=country, depth=depth, question=question.id
                    ):
                        self.assertTrue(
                            set(question.dependencies).issubset(selected_ids)
                        )

    def test_high_risk_items_have_specific_machine_readable_fields(self):
        questions = {question.id: question for question in self.catalog.all_questions()}
        expected_fields = {
            "fdd02.management_experience": {
                "management.five_year_employment_history",
                "management.employment_start_end_dates",
                "management.position_location",
            },
            "fdd03.litigation": {
                "legal.felony_convictions_and_nolo_pleas_10y",
                "legal.currently_effective_injunctions_and_restrictive_orders",
            },
            "fdd04.bankruptcy": {
                "legal.bankruptcy_10y_period_checked",
                "legal.principal_officer_or_general_partner_other_entity_bankruptcy",
                "legal.other_entity_role_and_one_year_timing",
            },
            "fdd20.pipeline_and_references": {
                "franchisees.current_business_reference_roster",
                "franchisees.former_exit_date_and_reason",
            },
            "us.delivery_and_updates": {
                "us_process.fdd_delivered_at",
                "us_process.binding_agreement_signed_at",
                "us_process.payment_made_at",
                "us_process.final_agreement_signed_at",
                "us_process.disclosure_version_retention_3y",
            },
        }

        for question_id, fields in expected_fields.items():
            with self.subTest(question=question_id):
                self.assertTrue(fields.issubset(set(questions[question_id].target_fields)))

    def test_unknown_search_query_placeholder_is_rejected_at_load_time(self):
        payload = self.catalog.all_questions()[0].model_dump(mode="json")
        payload["search_query_templates"] = [
            "{brand} franchise filing {unsupported_placeholder}"
        ]

        with self.assertRaisesRegex(ValidationError, "unsupported placeholders"):
            CatalogQuestion.model_validate(payload)
