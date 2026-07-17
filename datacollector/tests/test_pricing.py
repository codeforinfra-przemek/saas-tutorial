from decimal import Decimal
from unittest import TestCase

from datacollector.llm.pricing import estimate_standard_token_cost
from datacollector.schemas import TokenUsage


class TokenCostTests(TestCase):
    def test_terra_standard_cost_separates_all_input_classes_and_output(self):
        usage = TokenUsage(
            input_tokens=1500,
            cached_input_tokens=500,
            cache_write_input_tokens=200,
            output_tokens=200,
            reasoning_tokens=50,
            total_tokens=1700,
        )

        estimate = estimate_standard_token_cost(
            "gpt-5.6-terra",
            usage,
            service_tier="default",
        )

        self.assertIsNotNone(estimate)
        self.assertEqual(estimate.uncached_input_cost_usd, Decimal("0.00200000"))
        self.assertEqual(estimate.cached_input_cost_usd, Decimal("0.00012500"))
        self.assertEqual(
            estimate.cache_write_input_cost_usd,
            Decimal("0.00062500"),
        )
        self.assertEqual(estimate.output_cost_usd, Decimal("0.00300000"))
        self.assertEqual(estimate.total_estimated_cost_usd, Decimal("0.00575000"))

    def test_unknown_model_preserves_tokens_without_inventing_price(self):
        usage = TokenUsage(
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
        )

        self.assertIsNone(
            estimate_standard_token_cost(
                "unknown-model",
                usage,
                service_tier="default",
            )
        )
