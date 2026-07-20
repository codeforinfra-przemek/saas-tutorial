"""Runtime configuration for the standalone data collector."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_ENV_PATH = PACKAGE_ROOT / ".env"
ALLOWED_REASONING_EFFORTS = {
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
}
ALLOWED_SEARCH_CONTEXT_SIZES = {"low", "medium", "high"}
DEFAULT_WEB_SEARCH_BLOCKED_DOMAINS = (
    "arxiv.org",
    "quora.com",
    "reddit.com",
    "wikipedia.org",
)


class ConfigurationError(ValueError):
    """Raised when collector configuration is missing or invalid."""


def _read_int(name: str, default: int, minimum: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer.") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}.")
    return value


def _read_domains(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw_value = os.getenv(name)
    values = default if raw_value is None else tuple(raw_value.split(","))
    domains: list[str] = []
    for raw_domain in values:
        domain = raw_domain.strip().lower().rstrip(".")
        if not domain:
            continue
        if (
            "://" in domain
            or "/" in domain
            or " " in domain
            or "." not in domain
        ):
            raise ConfigurationError(
                f"{name} entries must be bare domains without paths or schemes."
            )
        if domain not in domains:
            domains.append(domain)
    if len(domains) > 100:
        raise ConfigurationError(f"{name} supports at most 100 domains.")
    return tuple(domains)


@dataclass(frozen=True)
class OpenAISettings:
    """OpenAI settings whose representation never exposes the API key."""

    api_key: str = field(repr=False)
    model: str = "gpt-5.6-terra"
    reasoning_effort: str = "medium"
    timeout_seconds: int = 60
    search_timeout_seconds: int = 180
    max_retries: int = 2
    max_output_tokens: int = 8000
    search_context_size: str = "low"
    web_search_blocked_domains: tuple[str, ...] = (
        DEFAULT_WEB_SEARCH_BLOCKED_DOMAINS
    )

    @classmethod
    def from_env(
        cls,
        env_path: Path | str = DEFAULT_ENV_PATH,
        *,
        require_api_key: bool = True,
    ) -> "OpenAISettings":
        load_dotenv(dotenv_path=Path(env_path), override=False)

        # `openai_apikey` is supported temporarily because it already exists in
        # the user's local file. New environments must use OPENAI_API_KEY.
        api_key = (
            os.getenv("OPENAI_API_KEY", "").strip()
            or os.getenv("openai_apikey", "").strip()
        )
        if require_api_key and not api_key:
            raise ConfigurationError(
                "Missing OPENAI_API_KEY in the environment or datacollector/.env."
            )

        model = os.getenv("OPENAI_MODEL", "gpt-5.6-terra").strip()
        if not model:
            raise ConfigurationError("OPENAI_MODEL cannot be empty.")

        reasoning_effort = os.getenv(
            "OPENAI_REASONING_EFFORT", "medium"
        ).strip().lower()
        if reasoning_effort not in ALLOWED_REASONING_EFFORTS:
            allowed = ", ".join(sorted(ALLOWED_REASONING_EFFORTS))
            raise ConfigurationError(
                f"OPENAI_REASONING_EFFORT must be one of: {allowed}."
            )

        search_context_size = os.getenv(
            "OPENAI_WEB_SEARCH_CONTEXT_SIZE", "low"
        ).strip().lower()
        if search_context_size not in ALLOWED_SEARCH_CONTEXT_SIZES:
            allowed = ", ".join(sorted(ALLOWED_SEARCH_CONTEXT_SIZES))
            raise ConfigurationError(
                "OPENAI_WEB_SEARCH_CONTEXT_SIZE must be one of: " f"{allowed}."
            )

        timeout_seconds = _read_int("OPENAI_TIMEOUT_SECONDS", 60, 1)
        return cls(
            api_key=api_key,
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=timeout_seconds,
            search_timeout_seconds=_read_int(
                "OPENAI_SEARCH_TIMEOUT_SECONDS",
                max(timeout_seconds, 180),
                1,
            ),
            max_retries=_read_int("OPENAI_MAX_RETRIES", 2, 0),
            max_output_tokens=_read_int("OPENAI_MAX_OUTPUT_TOKENS", 8000, 256),
            search_context_size=search_context_size,
            web_search_blocked_domains=_read_domains(
                "OPENAI_WEB_SEARCH_BLOCKED_DOMAINS",
                DEFAULT_WEB_SEARCH_BLOCKED_DOMAINS,
            ),
        )
