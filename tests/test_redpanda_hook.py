from dataclasses import replace
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from box import Box
from mako.template import Template

from App import App, ClusterApp, RoleApp


REGISTRY_PATH = Path(__file__).parents[1] / "demo" / "app-registry" / "redpanda"
HOOK_TEMPLATE = REGISTRY_PATH / "migration" / "hook.sh.mako"
ACL_MAP_TEMPLATE = REGISTRY_PATH / "migration" / "acl-map.sh.mako"


class RedpandaHookRenderTests(unittest.TestCase):
    def render(self, broker_count: int) -> tuple[str, str]:
        role = RoleApp(name="broker", ports=[])
        instances = [
            App(name=f"redpanda-{index}", role=role, cpu=500, ram=512)
            for index in range(1, broker_count + 1)
        ]
        cluster = ClusterApp(
            name="redpanda",
            image=None,
            preferences={"admin_user": "admin", "admin_pass": "secret"},
            instances_app=instances,
        )
        template_cluster = replace(cluster, preferences=Box(cluster.preferences))
        context = {
            "node": SimpleNamespace(private_ip="10.0.0.1"),
            "instance": instances[0],
            "role": role,
            "cluster": template_cluster,
            "apps": {},
        }
        hook = Template(filename=str(HOOK_TEMPLATE)).render(**context)
        acl_map = Template(filename=str(ACL_MAP_TEMPLATE)).render(**context)
        return hook, acl_map

    def run_config_validation(
        self,
        broker_count: int,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        hook, acl_map = self.render(broker_count)
        validation_script, marker, _ = hook.partition("wait_for_cluster || exit 1")
        self.assertTrue(marker)
        validation_script += (
            '\nprintf "%s:%s:%s\\n" '
            '"$BROKER_COUNT" '
            '"$DEFAULT_TOPIC_REPLICATION_FACTOR" '
            '"$DEFAULT_TOPIC_MIN_INSYNC_REPLICAS"\n'
        )

        with TemporaryDirectory() as directory:
            Path(directory, "acl-map.sh").write_text(acl_map)
            return subprocess.run(
                ["bash"],
                cwd=directory,
                input=validation_script,
                text=True,
                capture_output=True,
                env={**os.environ, **(environment or {})},
                check=False,
            )

    def test_defaults_follow_broker_count(self):
        for broker_count, replication, min_isr in (
            (1, 1, 1),
            (2, 2, 1),
            (3, 3, 2),
            (5, 3, 2),
        ):
            with self.subTest(broker_count=broker_count):
                hook, acl_map = self.render(broker_count)

                self.assertIn(f"BROKER_COUNT={broker_count}", hook)
                self.assertIn(
                    "${DEFAULT_TOPIC_REPLICATION_FACTOR}",
                    acl_map,
                )
                self.assertIn(
                    "${DEFAULT_TOPIC_MIN_INSYNC_REPLICAS}",
                    acl_map,
                )

                result = self.run_config_validation(broker_count)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(
                    result.stdout.rstrip().endswith(
                        f"{broker_count}:{replication}:{min_isr}"
                    ),
                    result.stdout,
                )

    def test_environment_overrides_topic_defaults(self):
        cases = (
            (
                {
                    "DEFAULT_TOPIC_REPLICATION_FACTOR": "2",
                    "DEFAULT_TOPIC_MIN_INSYNC_REPLICAS": "2",
                },
                2,
            ),
            ({"DEFAULT_TOPIC_REPLICATION_FACTOR": "3"}, 2),
        )

        for environment, expected_min_isr in cases:
            with self.subTest(environment=environment):
                result = self.run_config_validation(3, environment)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(
                    result.stdout.rstrip().endswith(
                        "3:"
                        f"{environment['DEFAULT_TOPIC_REPLICATION_FACTOR']}:"
                        f"{expected_min_isr}"
                    ),
                    result.stdout,
                )

    def test_rejects_invalid_cluster_and_topic_limits(self):
        cases = (
            (
                2,
                {"DEFAULT_TOPIC_REPLICATION_FACTOR": "3"},
                "cannot exceed BROKER_COUNT",
            ),
            (
                3,
                {
                    "DEFAULT_TOPIC_REPLICATION_FACTOR": "2",
                    "DEFAULT_TOPIC_MIN_INSYNC_REPLICAS": "3",
                },
                "cannot exceed DEFAULT_TOPIC_REPLICATION_FACTOR",
            ),
            (
                3,
                {"DEFAULT_TOPIC_REPLICATION_FACTOR": "invalid"},
                "must be a positive integer",
            ),
            (
                3,
                {"EXPECTED_BROKERS": "2"},
                "cannot exceed EXPECTED_BROKERS",
            ),
        )

        for broker_count, environment, error in cases:
            with self.subTest(
                broker_count=broker_count,
                environment=environment,
            ):
                result = self.run_config_validation(
                    broker_count,
                    environment,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)

    def test_hook_owns_defaults_and_acl_map_stays_declarative(self):
        hook_template = HOOK_TEMPLATE.read_text()
        acl_map_template = ACL_MAP_TEMPLATE.read_text()

        self.assertNotIn("<%!", hook_template)
        self.assertIsNone(re.search(r"<%(?!text>)", hook_template))
        self.assertIn("BROKER_COUNT=${cluster.instance_count}", hook_template)
        self.assertLess(
            hook_template.index('DEFAULT_TOPIC_REPLICATION_FACTOR="${'),
            hook_template.index('source "$CONFIG_MAP_FILE"'),
        )
        self.assertNotIn("BROKER_COUNT <", acl_map_template)
        self.assertNotIn("if [[", acl_map_template)
        self.assertIn("declare -A TOPICS=(", acl_map_template)


if __name__ == "__main__":
    unittest.main()
