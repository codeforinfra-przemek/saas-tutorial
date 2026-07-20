import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from datacollector.config import ConfigurationError, OpenAISettings


MISSING_ENV_FILE = Path("/tmp/datacollector-test-does-not-exist.env")


class OpenAISettingsTests(TestCase):
    def test_standard_api_key_and_options_load_without_exposing_secret(self):
        environment = {
            "OPENAI_API_KEY": "sk-test-secret",
            "OPENAI_MODEL": "test-model",
            "OPENAI_REASONING_EFFORT": "high",
            "OPENAI_TIMEOUT_SECONDS": "12",
            "OPENAI_SEARCH_TIMEOUT_SECONDS": "181",
            "OPENAI_MAX_RETRIES": "0",
            "OPENAI_MAX_OUTPUT_TOKENS": "4096",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = OpenAISettings.from_env(MISSING_ENV_FILE)

        self.assertEqual(settings.api_key, "sk-test-secret")
        self.assertEqual(settings.model, "test-model")
        self.assertEqual(settings.reasoning_effort, "high")
        self.assertEqual(settings.timeout_seconds, 12)
        self.assertEqual(settings.search_timeout_seconds, 181)
        self.assertEqual(settings.max_retries, 0)
        self.assertEqual(settings.max_output_tokens, 4096)
        self.assertEqual(settings.search_context_size, "low")
        self.assertEqual(
            settings.web_search_blocked_domains,
            ("arxiv.org", "quora.com", "reddit.com", "wikipedia.org"),
        )
        self.assertNotIn("sk-test-secret", repr(settings))

    def test_search_timeout_defaults_to_at_least_three_minutes(self):
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "secret",
                "OPENAI_TIMEOUT_SECONDS": "240",
            },
            clear=True,
        ):
            settings = OpenAISettings.from_env(MISSING_ENV_FILE)

        self.assertEqual(settings.search_timeout_seconds, 240)

    def test_web_search_options_are_configurable(self):
        environment = {
            "OPENAI_API_KEY": "secret",
            "OPENAI_WEB_SEARCH_CONTEXT_SIZE": "high",
            "OPENAI_WEB_SEARCH_BLOCKED_DOMAINS": (
                " reddit.com,Example.org,reddit.com "
            ),
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = OpenAISettings.from_env(MISSING_ENV_FILE)

        self.assertEqual(settings.search_context_size, "high")
        self.assertEqual(
            settings.web_search_blocked_domains,
            ("reddit.com", "example.org"),
        )

    def test_invalid_web_search_options_fail_before_api_call(self):
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "secret",
                "OPENAI_WEB_SEARCH_CONTEXT_SIZE": "huge",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ConfigurationError, "CONTEXT_SIZE"):
                OpenAISettings.from_env(MISSING_ENV_FILE)

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "secret",
                "OPENAI_WEB_SEARCH_BLOCKED_DOMAINS": "https://reddit.com/r/test",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ConfigurationError, "bare domains"):
                OpenAISettings.from_env(MISSING_ENV_FILE)

    def test_legacy_key_name_is_temporarily_supported(self):
        with patch.dict(os.environ, {"openai_apikey": "legacy-secret"}, clear=True):
            settings = OpenAISettings.from_env(MISSING_ENV_FILE)

        self.assertEqual(settings.api_key, "legacy-secret")

    def test_missing_key_fails_when_api_is_required(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "OPENAI_API_KEY"):
                OpenAISettings.from_env(MISSING_ENV_FILE)

    def test_invalid_reasoning_effort_fails(self):
        environment = {
            "OPENAI_API_KEY": "secret",
            "OPENAI_REASONING_EFFORT": "impossible",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "REASONING_EFFORT"):
                OpenAISettings.from_env(MISSING_ENV_FILE)

    def test_minimal_reasoning_effort_is_available_for_compatible_models(self):
        environment = {
            "OPENAI_API_KEY": "secret",
            "OPENAI_REASONING_EFFORT": "minimal",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = OpenAISettings.from_env(MISSING_ENV_FILE)

        self.assertEqual(settings.reasoning_effort, "minimal")

    def test_output_token_limit_has_a_safe_minimum(self):
        environment = {
            "OPENAI_API_KEY": "secret",
            "OPENAI_MAX_OUTPUT_TOKENS": "10",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "MAX_OUTPUT_TOKENS"):
                OpenAISettings.from_env(MISSING_ENV_FILE)
