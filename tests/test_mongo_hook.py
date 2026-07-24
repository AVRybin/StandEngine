from dataclasses import replace
import os
from pathlib import Path
import stat
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from box import Box
from mako.template import Template

from App import App, ClusterApp, RoleApp


REGISTRY_PATH = Path(__file__).parents[1] / "demo" / "app-registry" / "mongo"
HOOK_TEMPLATE = REGISTRY_PATH / "hook" / "hook.sh.mako"
POD_TEMPLATE = REGISTRY_PATH / "mongo-instance.yml.mako"


class MongoHookTests(unittest.TestCase):
    def render_hook(self, member_count: int) -> str:
        role = RoleApp(name="member", ports=[])
        instances = [
            App(name=f"mongo-{index}", role=role, cpu=500, ram=512)
            for index in range(1, member_count + 1)
        ]
        cluster = ClusterApp(
            name="mongo",
            image=None,
            preferences={
                "admin_user": "admin",
                "admin_pass": "secret",
                "replica_set_name": "rs0",
            },
            instances_app=instances,
        )
        apps = {
            instance.name: SimpleNamespace(
                app=instance,
                node=SimpleNamespace(private_ip=f"10.0.0.{index}"),
                cluster=cluster,
            )
            for index, instance in enumerate(instances, start=1)
        }
        return Template(filename=str(HOOK_TEMPLATE)).render(
            node=apps[instances[0].name].node,
            instance=instances[0],
            role=role,
            cluster=replace(cluster, preferences=Box(cluster.preferences)),
            apps=apps,
        )

    def run_hook(
        self,
        member_count: int,
        scenario: str,
    ) -> subprocess.CompletedProcess:
        rendered = self.render_hook(member_count)
        fake_podman = """#!/bin/bash
joined="$*"
echo "$joined" >>"$PODMAN_LOG"

if [[ "$joined" == *"db.adminCommand"* ]]; then
  [[ "$PODMAN_SCENARIO" == "timeout" ]] && exit 1
  exit 0
fi
if [[ "$joined" == *"rs.conf()"* ]]; then
  case "$PODMAN_SCENARIO" in
    mismatch) exit 11 ;;
    exact) exit 0 ;;
    *) exit 10 ;;
  esac
fi
if [[ "$joined" == *"rs.initiate"* ]]; then
  exit 0
fi
if [[ "$joined" == *"rs.status()"* ]]; then
  echo "10.0.0.1:27017"
  exit 0
fi
exit 0
"""

        with TemporaryDirectory() as directory:
            root = Path(directory)
            hook_path = root / "hook.sh"
            podman_path = root / "podman"
            log_path = root / "podman.log"
            migration = root / "migration"
            migration.mkdir()
            (migration / "demo.items.json").write_text("[]")
            hook_path.write_text(rendered)
            podman_path.write_text(fake_podman)
            podman_path.chmod(podman_path.stat().st_mode | stat.S_IXUSR)

            result = subprocess.run(
                ["bash", str(hook_path)],
                cwd=root,
                text=True,
                capture_output=True,
                env={
                    **os.environ,
                    "PATH": f"{root}:{os.environ['PATH']}",
                    "PODMAN_LOG": str(log_path),
                    "PODMAN_SCENARIO": scenario,
                    "MONGO_READY_TIMEOUT": "1",
                    "MONGO_READY_INTERVAL": "0",
                },
                check=False,
            )
            result.podman_log = log_path.read_text() if log_path.exists() else ""
            return result

    def test_renders_every_declared_member(self):
        rendered = self.render_hook(3)

        self.assertIn("MONGO_MEMBER_COUNT=3", rendered)
        for index in range(1, 4):
            self.assertEqual(rendered.count(f'"10.0.0.{index}:27017"'), 1)

    def test_fresh_init_waits_for_members_and_imports_through_primary(self):
        result = self.run_hook(3, "fresh")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rs.initiate", result.podman_log)
        self.assertIn("mongoimport --host=10.0.0.1:27017", result.podman_log)
        self.assertNotIn("exec -it", result.podman_log)

    def test_existing_mismatched_set_fails_without_reconfiguration(self):
        result = self.run_hook(3, "mismatch")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Automatic reconfiguration is disabled", result.stderr)
        self.assertNotIn("rs.initiate", result.podman_log)
        self.assertNotIn("rs.add", result.podman_log)

    def test_existing_matching_set_skips_init(self):
        result = self.run_hook(3, "exact")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already has the expected members", result.stdout)
        self.assertNotIn("rs.initiate", result.podman_log)

    def test_unreachable_member_times_out_before_init(self):
        result = self.run_hook(3, "timeout")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not become reachable", result.stderr)
        self.assertNotIn("rs.initiate", result.podman_log)

    def test_shell_and_pod_templates_contain_required_guards(self):
        rendered = self.render_hook(1)
        shell_check = subprocess.run(
            ["bash", "-n"],
            input=rendered,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(shell_check.returncode, 0, shell_check.stderr)
        self.assertIn("disableSplitHorizonIPCheck=true", POD_TEMPLATE.read_text())
        self.assertIn("MONGO_MEMBER_COUNT > 7", rendered)


if __name__ == "__main__":
    unittest.main()
