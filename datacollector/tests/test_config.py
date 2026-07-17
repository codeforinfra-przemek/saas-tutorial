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
            "OPENAI_MAX_RETRIES": "0",
            "OPENAI_MAX_OUTPUT_TOKENS": "4096",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = OpenAISettings.from_env(MISSING_ENV_FILE)

        self.assertEqual(settings.api_key, "sk-test-secret")
        self.assertEqual(settings.model, "test-model")
        self.assertEqual(settings.reasoning_effort, "high")
        self.assertEqual(settings.timeout_seconds, 12)
        self.assertEqual(settings.max_retries, 0)
        self.assertEqual(settings.max_output_tokens, 4096)
        self.assertNotIn("sk-test-secret", repr(settings))

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
