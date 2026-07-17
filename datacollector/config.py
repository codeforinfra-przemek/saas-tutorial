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


@dataclass(frozen=True)
class OpenAISettings:
    """OpenAI settings whose representation never exposes the API key."""

    api_key: str = field(repr=False)
    model: str = "gpt-5.6-terra"
    reasoning_effort: str = "medium"
    timeout_seconds: int = 60
    max_retries: int = 2
    max_output_tokens: int = 8000

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

        return cls(
            api_key=api_key,
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=_read_int("OPENAI_TIMEOUT_SECONDS", 60, 1),
            max_retries=_read_int("OPENAI_MAX_RETRIES", 2, 0),
            max_output_tokens=_read_int("OPENAI_MAX_OUTPUT_TOKENS", 8000, 256),
        )
