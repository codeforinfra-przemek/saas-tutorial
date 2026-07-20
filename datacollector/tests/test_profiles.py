from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from pydantic import ValidationError

from datacollector.agents.planner import PlannerAgent, PlannerValidationError
from datacollector.catalog import load_question_catalog
from datacollector.profiles import (
    ProfileCatalogError,
    ResearchProfileCatalog,
    load_profile_catalog,
    materialize_profile,
    resolve_profile_definition,
)
from datacollector.schemas import (
    FieldAvailability,
    PlannerInput,
    ResearchPlan,
)


class ResearchProfileTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.question_catalog = load_question_catalog()
        cls.profile_catalog = load_profile_catalog()

    def materialized(self, profile_id):
        return materialize_profile(
            self.profile_catalog,
            self.question_catalog,
            profile_id,
        )

    def test_polish_profile_aliases_resolve_to_versioned_ids(self):
        expected = {
            "PL:L1": "PL:L1:v1",
            "pl:l2": "PL:L2:v1",
            "PL:L3:v1": "PL:L3:v1",
        }

        for alias, profile_id in expected.items():
            with self.subTest(alias=alias):
                self.assertEqual(
                    resolve_profile_definition(
                        self.profile_catalog, alias
                    ).profile_id,
                    profile_id,
                )

    def test_profile_sizes_are_explicit_release_contracts(self):
        expected = {
            "PL:L1": (13, 61),
            "PL:L2": (26, 179),
            "PL:L3": (34, 273),
        }

        for profile_id, (question_count, field_count) in expected.items():
            with self.subTest(profile=profile_id):
                snapshot, selected = self.materialized(profile_id)
                self.assertEqual(len(selected), question_count)
                self.assertEqual(
                    sum(len(question.target_fields) for _, question in selected),
                    field_count,
                )
                self.assertEqual(len(snapshot.questions), question_count)

    def test_levels_are_cumulative_for_questions_and_fields(self):
        materialized = {}
        for profile_id in ("PL:L1", "PL:L2", "PL:L3"):
            snapshot, selected = self.materialized(profile_id)
            materialized[profile_id] = (
                {question.id for _, question in selected},
                {
                    field.target_field
                    for question in snapshot.questions
                    for field in question.fields
                },
            )

        self.assertTrue(
            materialized["PL:L1"][0].issubset(materialized["PL:L2"][0])
        )
        self.assertTrue(
            materialized["PL:L2"][0].issubset(materialized["PL:L3"][0])
        )
        self.assertTrue(
            materialized["PL:L1"][1].issubset(materialized["PL:L2"][1])
        )
        self.assertTrue(
            materialized["PL:L2"][1].issubset(materialized["PL:L3"][1])
        )

    def test_l3_covers_fdd_without_system_or_us_questions(self):
        _, selected = self.materialized("PL:L3")
        question_ids = {question.id for _, question in selected}
        fdd_items = {item for _, question in selected for item in question.fdd_items}

        self.assertEqual(fdd_items, set(range(1, 24)))
        self.assertFalse(
            any(question_id.startswith("quality.") for question_id in question_ids)
        )
        self.assertFalse(
            any(question.jurisdiction.value == "us_only" for _, question in selected)
        )

    def test_every_selected_field_has_exactly_one_availability_policy(self):
        expected_availability = {
            "PL:L1": {
                "public_expected": 30,
                "public_optional": 31,
            },
            "PL:L2": {
                "public_expected": 30,
                "public_optional": 102,
                "registry_expected": 24,
                "manual_research_required": 23,
            },
            "PL:L3": {
                "public_expected": 37,
                "public_optional": 105,
                "registry_expected": 24,
                "private_document_required": 65,
                "manual_research_required": 40,
                "system_derived": 2,
            },
        }

        for profile_id, expected_counts in expected_availability.items():
            with self.subTest(profile=profile_id):
                snapshot, selected = self.materialized(profile_id)
                selected_by_id = {
                    question.id: question for _, question in selected
                }
                availability = Counter()
                for policy in snapshot.questions:
                    policy_fields = [field.target_field for field in policy.fields]
                    self.assertEqual(
                        policy_fields,
                        selected_by_id[policy.question_id].target_fields,
                    )
                    availability.update(
                        field.availability.value for field in policy.fields
                    )
                self.assertEqual(dict(availability), expected_counts)

    def test_public_registry_and_private_examples_are_distinct(self):
        l1, _ = self.materialized("PL:L1")
        l2, _ = self.materialized("PL:L2")
        l3, _ = self.materialized("PL:L3")

        def availability(snapshot, question_id, field_name):
            question = next(
                item for item in snapshot.questions if item.question_id == question_id
            )
            return next(
                field.availability
                for field in question.fields
                if field.target_field == field_name
            )

        self.assertEqual(
            availability(l1, "scope.brand_identity", "brand.name"),
            FieldAvailability.PUBLIC_EXPECTED,
        )
        self.assertEqual(
            availability(
                l2, "scope.brand_identity", "franchisor.registration_id"
            ),
            FieldAvailability.REGISTRY_EXPECTED,
        )
        self.assertEqual(
            availability(
                l3, "fdd09.franchisee_obligations", "obligations.matrix"
            ),
            FieldAvailability.PRIVATE_DOCUMENT_REQUIRED,
        )

    def test_profile_dependencies_are_closed(self):
        for profile_id in ("PL:L1", "PL:L2", "PL:L3"):
            _, selected = self.materialized(profile_id)
            selected_ids = {question.id for _, question in selected}
            for _, question in selected:
                with self.subTest(profile=profile_id, question=question.id):
                    self.assertTrue(set(question.dependencies).issubset(selected_ids))

    def test_common_task_ids_stay_stable_when_profile_is_upgraded(self):
        plans = {
            profile_id: PlannerAgent(
                self.question_catalog,
                profile_catalog=self.profile_catalog,
            ).create_plan(
                PlannerInput(
                    brand_name="Example",
                    target_country="PL",
                    profile_id=profile_id,
                )
            )
            for profile_id in ("PL:L1", "PL:L2", "PL:L3")
        }
        ids = {
            profile_id: {
                task.catalog_question_id: task.task_id for task in plan.tasks
            }
            for profile_id, plan in plans.items()
        }

        for question_id, task_id in ids["PL:L1"].items():
            self.assertEqual(ids["PL:L2"][question_id], task_id)
            self.assertEqual(ids["PL:L3"][question_id], task_id)

    def test_completion_gate_is_field_level_and_country_calibrated(self):
        expected_required_fields = {
            "PL:L1": 30,
            "PL:L2": 77,
            "PL:L3": 101,
        }

        for profile_id, expected_count in expected_required_fields.items():
            with self.subTest(profile=profile_id):
                snapshot, _ = self.materialized(profile_id)
                required_fields = {
                    field.target_field
                    for question in snapshot.questions
                    for field in question.fields
                    if field.required_for_completion
                }
                plan = PlannerAgent(
                    self.question_catalog,
                    profile_catalog=self.profile_catalog,
                ).create_plan(
                    PlannerInput(
                        brand_name="Example",
                        target_country="PL",
                        profile_id=profile_id,
                    )
                )
                self.assertEqual(len(required_fields), expected_count)
                self.assertEqual(set(plan.critical_fields), required_fields)

        l1_plan = PlannerAgent(
            self.question_catalog,
            profile_catalog=self.profile_catalog,
        ).create_plan(
            PlannerInput(
                brand_name="Example", target_country="PL", profile_id="PL:L1"
            )
        )
        self.assertNotIn("brand.aliases", l1_plan.critical_fields)
        self.assertNotIn("fees.joining_fee_tax_basis", l1_plan.critical_fields)
        self.assertNotIn("investment.startup_package", l1_plan.critical_fields)

    def test_l3_restores_full_catalog_semantics_without_weaker_evidence(self):
        l2_snapshot, _ = self.materialized("PL:L2")
        l3_snapshot, l3_selected = self.materialized("PL:L3")
        catalog_by_id = {
            question.id: question
            for question in self.question_catalog.all_questions()
        }
        l2_by_id = {
            question.question_id: question for question in l2_snapshot.questions
        }
        l3_policy_by_id = {
            question.question_id: question for question in l3_snapshot.questions
        }
        requirement_order = {
            "optional": 1,
            "recommended": 2,
            "required": 3,
            "critical": 4,
        }

        for _, question in l3_selected:
            canonical = catalog_by_id[question.id]
            self.assertEqual(question.title, canonical.title)
            self.assertEqual(question.question, canonical.question)
            self.assertEqual(question.dependencies, canonical.dependencies)
            self.assertEqual(
                question.evidence.acceptance_criteria,
                canonical.evidence.acceptance_criteria,
            )

        for question_id in set(l2_by_id) & set(l3_policy_by_id):
            with self.subTest(question=question_id):
                l2_policy = l2_by_id[question_id]
                l3_policy = l3_policy_by_id[question_id]
                self.assertGreaterEqual(l3_policy.min_sources, l2_policy.min_sources)
                if l2_policy.requires_independent_corroboration:
                    self.assertTrue(l3_policy.requires_independent_corroboration)
                self.assertGreaterEqual(
                    requirement_order[l3_policy.requirement.value],
                    requirement_order[l2_policy.requirement.value],
                )

    def test_country_profiles_use_polish_queries_instead_of_fdd_search_terms(self):
        for profile_id in ("PL:L1", "PL:L2", "PL:L3"):
            _, selected = self.materialized(profile_id)
            queries = [
                query
                for _, question in selected
                for query in question.search_query_templates
            ]
            with self.subTest(profile=profile_id):
                self.assertTrue(queries)
                self.assertFalse(any("FDD Item" in query for query in queries))
                self.assertTrue(
                    any(
                        token in " ".join(queries).lower()
                        for token in ("franczyza", "placów", "sprawozdanie")
                    )
                )

    def test_plan_freezes_canonical_profile_and_full_evidence_policy(self):
        plan = PlannerAgent(
            self.question_catalog,
            profile_catalog=self.profile_catalog,
        ).create_plan(
            PlannerInput(
                brand_name="Example",
                target_country="PL",
                profile_id="pl:l1",
            )
        )

        self.assertEqual(plan.planner_input.profile_id, "PL:L1:v1")
        self.assertEqual(plan.profile_snapshot.profile_id, "PL:L1:v1")
        task = plan.tasks[0]
        policy = plan.profile_snapshot.questions[0]
        self.assertEqual(task.preferred_source_types, policy.preferred_source_types)
        self.assertEqual(task.acceptance_criteria, policy.acceptance_criteria)

        payload = plan.model_dump(mode="json")
        payload["tasks"][0]["acceptance_criteria"] = "A different valid criterion."
        with self.assertRaisesRegex(ValidationError, "materialized policy"):
            ResearchPlan.model_validate(payload)

    def test_profile_hash_changes_with_effective_inherited_policy(self):
        original, _ = self.materialized("PL:L3")
        payload = self.profile_catalog.model_dump(mode="json")
        payload["profiles"][0]["question_rules"][0]["fields"][
            "brand.name"
        ] = "public_optional"
        changed_catalog = ResearchProfileCatalog.model_validate(payload)
        changed, _ = materialize_profile(
            changed_catalog,
            self.question_catalog,
            "PL:L3",
        )

        self.assertNotEqual(original.profile_sha256, changed.profile_sha256)

    def test_profile_hash_changes_with_effective_query_contract(self):
        original, _ = self.materialized("PL:L1")
        payload = self.profile_catalog.model_dump(mode="json")
        payload["profiles"][0]["question_rules"][0][
            "search_query_templates"
        ][0] = '"{brand}" zmieniona kwerenda profilu {country}'
        changed_catalog = ResearchProfileCatalog.model_validate(payload)
        changed, _ = materialize_profile(
            changed_catalog,
            self.question_catalog,
            "PL:L1",
        )

        self.assertNotEqual(original.profile_sha256, changed.profile_sha256)

    def test_plan_rejects_tampered_snapshot_and_legacy_profile_schema(self):
        plan = PlannerAgent(
            self.question_catalog,
            profile_catalog=self.profile_catalog,
        ).create_plan(
            PlannerInput(
                brand_name="Example", target_country="PL", profile_id="PL:L1"
            )
        )
        tampered = plan.model_dump(mode="json")
        tampered["profile_snapshot"]["questions"][0][
            "acceptance_criteria"
        ] = "Zmodyfikowane kryterium, którego hash nie obejmuje."
        with self.assertRaisesRegex(ValidationError, "SHA-256"):
            ResearchPlan.model_validate(tampered)

        legacy_version = plan.model_dump(mode="json")
        legacy_version["schema_version"] = "1.2.0"
        with self.assertRaisesRegex(ValidationError, "schema version 1.3.0"):
            ResearchPlan.model_validate(legacy_version)

    def test_profile_country_mismatch_is_rejected_before_provider_use(self):
        with self.assertRaisesRegex(PlannerValidationError, "is for PL, not DE"):
            PlannerAgent(
                self.question_catalog,
                profile_catalog=self.profile_catalog,
            ).create_plan(
                PlannerInput(
                    brand_name="Example",
                    target_country="DE",
                    profile_id="PL:L1",
                )
            )

    def test_profile_yaml_rejects_duplicate_mapping_keys(self):
        with TemporaryDirectory() as directory:
            profile_path = Path(directory) / "profiles.yaml"
            profile_path.write_text(
                'version: "2.0.0"\nversion: "2.0.1"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ProfileCatalogError, "Cannot load research profile catalog"
            ):
                load_profile_catalog(profile_path)
