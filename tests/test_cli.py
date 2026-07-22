from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
import unittest

import main


class CliTests(unittest.TestCase):
    def test_parse_create_command(self):
        is_destroy, manifest = main.parse_args(["stands-engine", "create", "demo/stand.yml"])

        self.assertFalse(is_destroy)
        self.assertEqual(str(manifest), "demo/stand.yml")

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


if __name__ == "__main__":
    unittest.main()
