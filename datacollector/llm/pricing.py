"""Versioned OpenAI token price estimates for auditable agent runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..schemas import CostEstimate, TokenUsage


PRICING_SOURCE = "https://developers.openai.com/api/docs/pricing"
PRICING_AS_OF = date(2026, 7, 17)
RATE_CARD_ID = "openai-standard-2026-07-17"
MILLION = Decimal("1000000")
MONEY_QUANTUM = Decimal("0.00000001")


@dataclass(frozen=True)
class TokenRates:
    input_usd_per_million: Decimal
    cached_input_usd_per_million: Decimal
    cache_write_usd_per_million: Decimal
    output_usd_per_million: Decimal


# Standard direct-API prices per 1M tokens.
STANDARD_TOKEN_RATES = {
    "gpt-5.6-sol": TokenRates(
        Decimal("5"), Decimal("0.5"), Decimal("6.25"), Decimal("30")
    ),
    "gpt-5.6-terra": TokenRates(
        Decimal("2.5"), Decimal("0.25"), Decimal("3.125"), Decimal("15")
    ),
    "gpt-5.6-luna": TokenRates(
        Decimal("1"), Decimal("0.1"), Decimal("1.25"), Decimal("6")
    ),
}


def _rates_for_model(model: str) -> TokenRates | None:
    for model_family, rates in STANDARD_TOKEN_RATES.items():
        if model == model_family or model.startswith(f"{model_family}-"):
            return rates
    return None


def estimate_standard_token_cost(
    model: str,
    usage: TokenUsage,
    *,
    service_tier: str | None,
) -> CostEstimate | None:
    """Estimate a standard-tier cost; return None for unknown pricing contexts."""

    if service_tier not in (None, "default"):
        return None
    rates = _rates_for_model(model)
    if rates is None:
        return None

    uncached_tokens = (
        usage.input_tokens
        - usage.cached_input_tokens
        - usage.cache_write_input_tokens
    )
    uncached_cost = (
        Decimal(uncached_tokens) * rates.input_usd_per_million / MILLION
    ).quantize(MONEY_QUANTUM)
    cached_cost = (
        Decimal(usage.cached_input_tokens)
        * rates.cached_input_usd_per_million
        / MILLION
    ).quantize(MONEY_QUANTUM)
    cache_write_cost = (
        Decimal(usage.cache_write_input_tokens)
        * rates.cache_write_usd_per_million
        / MILLION
    ).quantize(MONEY_QUANTUM)
    output_cost = (
        Decimal(usage.output_tokens) * rates.output_usd_per_million / MILLION
    ).quantize(MONEY_QUANTUM)

    return CostEstimate(
        rate_card_id=RATE_CARD_ID,
        pricing_source=PRICING_SOURCE,
        pricing_as_of=PRICING_AS_OF,
        input_usd_per_million=rates.input_usd_per_million,
        cached_input_usd_per_million=rates.cached_input_usd_per_million,
        cache_write_usd_per_million=rates.cache_write_usd_per_million,
        output_usd_per_million=rates.output_usd_per_million,
        uncached_input_cost_usd=uncached_cost,
        cached_input_cost_usd=cached_cost,
        cache_write_input_cost_usd=cache_write_cost,
        output_cost_usd=output_cost,
        total_estimated_cost_usd=(
            uncached_cost + cached_cost + cache_write_cost + output_cost
        ),
        assumptions=[
            "Standard direct-API token pricing; no Batch, Flex, or Priority tier.",
            "No regional-processing uplift or tool fee.",
            "Any provider-reported cache-write tokens are priced separately.",
            "Reasoning tokens are included in output_tokens and are not charged twice.",
        ],
    )
