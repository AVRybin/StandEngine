from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from InfraBaseLib.SShExecutor.executor import SShExecutor
from InfraBaseLib.SShExecutor.diagnostic import PyinfraDiagnostic, PyinfraHostStats
from InfraBaseLib.metal_provision.provision import MetalProvision


class DiagnosticStreamsTests(unittest.TestCase):
    def test_pulumi_events_only_write_to_stderr(self):
        metadata = SimpleNamespace(
            type="hcloud:index/server:Server",
            op="create",
            urn="urn:pulumi:test::demo::hcloud:index/server:Server::node-1",
            diffs=["server_type"],
            detailed_diff=None,
            olds={},
            news={"server_type": "cx22"},
        )
        event = SimpleNamespace(
            resource_pre_event=SimpleNamespace(metadata=metadata),
            diagnostic_event=SimpleNamespace(severity="warning", message="warning text"),
            res_outputs_event=None,
            res_op_failed_event=None,
            summary_event=SimpleNamespace(resource_changes={"create": 1}),
        )
        provision = object.__new__(MetalProvision)
        stdout = StringIO()
        stderr = StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            provision.event_handler(event)

        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("[DIFF]", stderr.getvalue())
        self.assertIn("[pulumi:warning] warning text", stderr.getvalue())
        self.assertIn("[pulumi] create:", stderr.getvalue())
        self.assertIn("[pulumi] summary:", stderr.getvalue())

    def test_pyinfra_diagnostics_only_write_to_stderr(self):
        diagnostic = PyinfraDiagnostic()
        stats = PyinfraHostStats(expected_operations=1, started=1, completed=1, success=1)
        stdout = StringIO()
        stderr = StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            diagnostic.print_host_summary("10.0.0.2", stats, status="complete")

        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("[pyinfra-host-summary]", stderr.getvalue())
        self.assertIn("host=10.0.0.2", stderr.getvalue())

    def test_pyinfra_failed_host_raises_runtime_error(self):
        executor = object.__new__(SShExecutor)
        failed_host = type("FailedHost", (), {"name": "10.0.0.3"})()
        executor.state = SimpleNamespace(
            failed_hosts={failed_host},
            inventory=SimpleNamespace(),
        )
        executor.uploader = SimpleNamespace(upload_files=[])

        with (
            patch("InfraBaseLib.SShExecutor.executor.connect_all"),
            patch("InfraBaseLib.SShExecutor.executor.run_ops"),
            patch("InfraBaseLib.SShExecutor.executor.disconnect_all") as disconnect_all,
            self.assertRaisesRegex(RuntimeError, "PyInfra failed on hosts: 10.0.0.3"),
        ):
            executor.run([])

        disconnect_all.assert_called_once_with(executor.state)


if __name__ == "__main__":
    unittest.main()
