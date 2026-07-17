from unittest import TestCase

from datacollector.loop import LOOP_SEQUENCE, LoopAgent, LoopPolicy


class LoopBlueprintTests(TestCase):
    def test_blueprint_begins_with_planner_and_requires_human_gate(self):
        policy = LoopPolicy()

        self.assertEqual(LOOP_SEQUENCE[0], LoopAgent.PLANNER)
        self.assertLess(
            LOOP_SEQUENCE.index(LoopAgent.HUMAN_REVIEW),
            LOOP_SEQUENCE.index(LoopAgent.IMPORTER),
        )
        self.assertTrue(policy.require_human_review_before_import)
        self.assertFalse(policy.publish_automatically)
