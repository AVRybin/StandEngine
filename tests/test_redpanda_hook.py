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
CONFIG_MAP = """\
declare -A TOPICS=(
  ["test-topic"]="1:${DEFAULT_TOPIC_REPLICATION_FACTOR}:60000:${DEFAULT_TOPIC_MIN_INSYNC_REPLICAS}"
)
declare -A USERS=()
declare -A ACL_GROUP_RULES=()
declare -A ACL_TOPIC_RULES=()
"""


class RedpandaHookRenderTests(unittest.TestCase):
    def render(self, broker_count: int) -> str:
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
        return Template(filename=str(HOOK_TEMPLATE)).render(**context)

    def run_hook(
        self,
        broker_count: int,
        config_map: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        hook = self.render(broker_count)

        with TemporaryDirectory() as directory:
            if config_map is not None:
                Path(directory, "acl-map.sh").write_text(config_map)

            return subprocess.run(
                ["bash"],
                cwd=directory,
                input=hook,
                text=True,
                capture_output=True,
                env={**os.environ, **(environment or {})},
                check=False,
            )

    def run_config_validation(
        self,
        broker_count: int,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        hook = self.render(broker_count)
        validation_script, marker, _ = hook.partition("wait_for_cluster || exit 1")
        self.assertTrue(marker)
        validation_script += (
            '\nprintf "%s:%s:%s\\n" '
            '"$BROKER_COUNT" '
            '"$DEFAULT_TOPIC_REPLICATION_FACTOR" '
            '"$DEFAULT_TOPIC_MIN_INSYNC_REPLICAS"\n'
        )

        with TemporaryDirectory() as directory:
            Path(directory, "acl-map.sh").write_text(CONFIG_MAP)
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
                hook = self.render(broker_count)

                self.assertIn(f"BROKER_COUNT={broker_count}", hook)
                self.assertIn(
                    "${DEFAULT_TOPIC_REPLICATION_FACTOR}",
                    CONFIG_MAP,
                )
                self.assertIn(
                    "${DEFAULT_TOPIC_MIN_INSYNC_REPLICAS}",
                    CONFIG_MAP,
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

    def test_missing_and_empty_default_config_are_noop(self):
        for config_map in (None, ""):
            with self.subTest(config_map=config_map):
                result = self.run_hook(3, config_map=config_map)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(
                    "No Redpanda migration actions configured; nothing to do",
                    result.stdout,
                )
                self.assertNotIn("Waiting for cluster", result.stdout)

    def test_missing_explicit_config_fails(self):
        result = self.run_hook(
            3,
            environment={"CONFIG_MAP_FILE": "./missing-config.sh"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Config map not found: ./missing-config.sh", result.stderr)

    def test_invalid_explicit_config_fails(self):
        result = self.run_hook(
            3,
            config_map="invalid syntax (\n",
            environment={"CONFIG_MAP_FILE": "./acl-map.sh"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Failed to load config map: ./acl-map.sh", result.stderr)

    def test_hook_owns_defaults_and_optional_config_contract(self):
        hook_template = HOOK_TEMPLATE.read_text()

        self.assertNotIn("<%!", hook_template)
        self.assertIsNone(re.search(r"<%(?!text>)", hook_template))
        self.assertIn("BROKER_COUNT=${cluster.instance_count}", hook_template)
        self.assertLess(
            hook_template.index('DEFAULT_TOPIC_REPLICATION_FACTOR="${'),
            hook_template.index('source "$CONFIG_MAP_FILE"'),
        )
        self.assertIn("declare -A TOPICS=()", hook_template)
        self.assertIn('if [[ -f "$CONFIG_MAP_FILE" ]]', hook_template)
        self.assertIn(
            "No Redpanda migration actions configured; nothing to do",
            hook_template,
        )

    def test_rendered_hook_and_config_fixture_have_valid_syntax(self):
        for name, content in (
            ("hook.sh", self.render(3)),
            ("config-map.sh", CONFIG_MAP),
        ):
            with self.subTest(name=name):
                result = subprocess.run(
                    ["bash", "-n"],
                    input=content,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
