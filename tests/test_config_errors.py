import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from config.config import Config
from config.errors import ConfigurationError, load_settings
from StandFramework.config.config import ConfigBackend


VALID_STAND_ENV = {
    "STAND__USER": "owner",
    "STAND__PASSPHRASE": "passphrase",
    "STAND__PATH_TO_KEY": "/tmp/id_ed25519",
    "STAND__PATH_TO_CONFIGSET": "/tmp/configsets",
}


class ConfigurationErrorTests(unittest.TestCase):
    def test_missing_nested_section_expands_required_environment_variables(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigurationError) as raised:
                load_settings(Config)

        self.assertEqual(
            str(raised.exception),
            "Configuration error:\n"
            "- STAND__USER: required\n"
            "- STAND__PASSPHRASE: required\n"
            "- STAND__PATH_TO_KEY: required\n"
            "- STAND__PATH_TO_CONFIGSET: required",
        )

    def test_partial_section_only_lists_missing_variables(self):
        with patch.dict(os.environ, {"STAND__USER": "owner"}, clear=True):
            with self.assertRaises(ConfigurationError) as raised:
                load_settings(Config)

        message = str(raised.exception)
        self.assertNotIn("STAND__USER", message)
        self.assertIn("- STAND__PASSPHRASE: required", message)
        self.assertIn("- STAND__PATH_TO_KEY: required", message)
        self.assertIn("- STAND__PATH_TO_CONFIGSET: required", message)

    def test_invalid_value_is_not_repeated_in_error_message(self):
        invalid_value = "sensitive-invalid-value"
        environment = {
            **VALID_STAND_ENV,
            "OUTPUT__CONSOLE": invalid_value,
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(ConfigurationError) as raised:
                load_settings(Config)

        self.assertEqual(
            str(raised.exception),
            "Configuration error:\n"
            "- OUTPUT__CONSOLE: expected a valid boolean",
        )
        self.assertNotIn(invalid_value, str(raised.exception))
        self.assertNotIn("errors.pydantic.dev", str(raised.exception))

    def test_model_validator_keeps_actionable_environment_name(self):
        environment = {**VALID_STAND_ENV, "OUTPUT__FILE": "true"}
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(ConfigurationError) as raised:
                load_settings(Config)

        self.assertEqual(
            str(raised.exception),
            "Configuration error:\n"
            "- OUTPUT__FILE_PATH: required when OUTPUT__FILE=true",
        )

    def test_model_validator_reports_file_path_that_is_not_a_directory(self):
        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "result.json"
            output_path.touch()
            environment = {
                **VALID_STAND_ENV,
                "OUTPUT__FILE": "true",
                "OUTPUT__FILE_PATH": str(output_path),
            }
            with patch.dict(os.environ, environment, clear=True):
                with self.assertRaises(ConfigurationError) as raised:
                    load_settings(Config)

        self.assertEqual(
            str(raised.exception),
            "Configuration error:\n"
            "- OUTPUT__FILE_PATH: must point to a directory",
        )

    def test_backend_sections_expand_without_exposing_values(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigurationError) as raised:
                load_settings(ConfigBackend)

        self.assertEqual(
            str(raised.exception),
            "Configuration error:\n"
            "- HCLOUD__TOKEN: required\n"
            "- S3__ACCESS_KEY: required\n"
            "- S3__SECRET_KEY: required\n"
            "- S3__REGION: required\n"
            "- S3__ENDPOINT: required\n"
            "- S3__BUCKET: required",
        )

    def test_malformed_nested_setting_has_concise_error(self):
        with patch.dict(os.environ, {"STAND": "not-json"}, clear=True):
            with self.assertRaises(ConfigurationError) as raised:
                load_settings(Config)

        self.assertEqual(
            str(raised.exception),
            "Configuration error:\n"
            "- STAND: could not parse the nested setting",
        )


if __name__ == "__main__":
    unittest.main()
