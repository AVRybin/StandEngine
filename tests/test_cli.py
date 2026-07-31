from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import json
import unittest

import main
from config.config import Config


class CliTests(unittest.TestCase):
    def test_parse_create_command(self):
        is_destroy, manifest = main.parse_args(
            ["stands-engine", "create", "demo/stand/stand.yml"]
        )

        self.assertFalse(is_destroy)
        self.assertEqual(str(manifest), "demo/stand/stand.yml")

    def test_version_does_not_load_configuration(self):
        output = StringIO()
        with (
            patch.object(main, "Config") as config,
            self.assertRaises(SystemExit) as raised,
            redirect_stdout(output),
        ):
            main.main(["stands-engine", "--version"])

        self.assertEqual(raised.exception.code, 0)
        self.assertRegex(output.getvalue(), r"^stands-engine \d+\.\d+\.\d+\n$")
        config.assert_not_called()

    def test_parse_named_resource(self):
        with TemporaryDirectory() as directory:
            resources = main.parse_resource_roots([f"project={directory}"])

        self.assertEqual(resources, {"project": Path(directory).resolve()})

    def test_duplicate_resource_is_rejected(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "specified more than once"):
                main.parse_resource_roots(
                    [f"project={directory}", f"project={directory}"]
                )

    def test_invalid_arguments_exit_with_code_2(self):
        error = StringIO()
        with (
            self.assertRaises(SystemExit) as raised,
            redirect_stderr(error),
        ):
            main.main(["stands-engine", "invalid", "stand.yml"])

        self.assertEqual(raised.exception.code, 2)
        self.assertTrue(error.getvalue())

    def test_runtime_error_returns_1_and_only_writes_stderr(self):
        stdout = StringIO()
        stderr = StringIO()
        with (
            patch.object(main, "Config", side_effect=RuntimeError("configuration failed")),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main.main(["stands-engine", "create", "stand.yml"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "configuration failed\n")

    def test_configuration_validation_error_is_formatted_for_cli(self):
        stdout = StringIO()
        stderr = StringIO()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(main, "Config", Config),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main.main(["stands-engine", "validate", "stand.yml"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "Configuration error:\n"
            "- STAND__USER: required\n"
            "- STAND__PASSPHRASE: required\n"
            "- STAND__PATH_TO_KEY: required\n"
            "- STAND__PATH_TO_CONFIGSET: required\n",
        )

    def test_keyboard_interrupt_returns_130(self):
        stdout = StringIO()
        stderr = StringIO()
        with (
            patch.object(main, "Config", side_effect=KeyboardInterrupt),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main.main(["stands-engine", "create", "stand.yml"])

        self.assertEqual(exit_code, 130)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "Interrupted\n")

    def test_successful_destroy_outputs_result_after_destroy(self):
        with TemporaryDirectory() as directory:
            private_key = Path(directory) / "id_ed25519"
            config = SimpleNamespace(stand=SimpleNamespace(path_to_key=private_key))
            stand = unittest.mock.Mock()

            with (
                patch.object(main, "Config", return_value=config),
                patch.object(main, "parse_manifest", return_value={}),
                patch.object(main, "build_stand", return_value=stand),
            ):
                exit_code = main.main(["stands-engine", "destroy", "stand.yml"])

        self.assertEqual(exit_code, 0)
        stand.destroy.assert_called_once_with()
        stand.output_destroy_result.assert_called_once_with()

    def test_validate_runs_preflight_and_emits_success_ndjson(self):
        with TemporaryDirectory() as directory:
            private_key = Path(directory) / "id_ed25519"
            config = SimpleNamespace(stand=SimpleNamespace(path_to_key=private_key))
            stand = unittest.mock.Mock()
            stand.result_ndjson.side_effect = lambda value: json.dumps(value) + "\n"
            stdout = StringIO()

            with (
                patch.object(main, "Config", return_value=config),
                patch.object(main, "parse_manifest", return_value={}) as parse_manifest,
                patch.object(main, "build_stand", return_value=stand),
                redirect_stdout(stdout),
            ):
                exit_code = main.main(["stands-engine", "validate", "stand.yml"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"operation": "validate", "status": "success"})
        parse_manifest.assert_called_once_with(
            Path("stand.yml"), operation="create", resource_roots={}
        )
        stand.validate_preflight.assert_called_once_with()
        stand.up.assert_not_called()
        stand.destroy.assert_not_called()

    def test_create_preflight_failure_happens_before_key_write_and_up(self):
        with TemporaryDirectory() as directory:
            private_key = Path(directory) / "id_ed25519"
            config = SimpleNamespace(stand=SimpleNamespace(path_to_key=private_key))
            stand = unittest.mock.Mock()
            stand.validate_preflight.side_effect = ValueError("preflight failed")
            stderr = StringIO()

            with (
                patch.object(main, "Config", return_value=config),
                patch.object(main, "parse_manifest", return_value={}),
                patch.object(main, "build_stand", return_value=stand),
                redirect_stderr(stderr),
            ):
                exit_code = main.main(["stands-engine", "create", "stand.yml"])

            self.assertFalse(private_key.exists())

        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr.getvalue(), "preflight failed\n")
        stand.up.assert_not_called()

    def test_failed_destroy_returns_1_without_result(self):
        with TemporaryDirectory() as directory:
            private_key = Path(directory) / "id_ed25519"
            config = SimpleNamespace(stand=SimpleNamespace(path_to_key=private_key))
            stand = unittest.mock.Mock()
            stand.destroy.side_effect = RuntimeError("destroy failed")
            stdout = StringIO()
            stderr = StringIO()

            with (
                patch.object(main, "Config", return_value=config),
                patch.object(main, "parse_manifest", return_value={}),
                patch.object(main, "build_stand", return_value=stand),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = main.main(["stands-engine", "destroy", "stand.yml"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "destroy failed\n")
        stand.output_destroy_result.assert_not_called()


if __name__ == "__main__":
    unittest.main()
