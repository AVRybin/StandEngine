from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from mako.template import Template

from App import App, ClusterApp, RoleApp
from ManifestParser import _normalize_template_hook_and_connection_paths
from ManifestParser.validation import validate_manifest
from StandFramework import Stand
from StandFramework.stand.stand import InstanceApp
from config.config import OutputSettings


def valid_manifest() -> dict:
    return {
        "stand": {
            "project": "demo",
            "env": "test",
            "users": {"sudo": "admin", "app": "app"},
            "ssh": {"key_name_admin": "admin-key"},
        },
        "registries": {"docker": {"url": "docker.io"}},
        "apps": {
            "redis": {
                "name": "redis",
                "image": {"registry": "docker", "path": "redis", "version": "7"},
                "roles": {"server": {"ports": []}},
                "templates": {
                    "pod": {
                        "path": "redis.yml.mako",
                        "dest": "/home/app/redis.yml",
                        "owner": "app",
                        "mode": "644",
                    }
                },
                "instances": {
                    "redis-1": {"role": "server", "cpu": 500, "ram": 512}
                },
            }
        },
        "node_profiles": {
            "default": {
                "location": "hel1",
                "type_serv": "cpx32",
                "image": "rocky-10",
                "network": "demo-network",
                "cloud-init": "cloud-init.yaml.mako",
            }
        },
        "nodes": {"node-1": {"apps": ["redis-1"]}},
    }


class ConnectionManifestTests(unittest.TestCase):
    def test_connection_is_optional(self):
        validate_manifest(valid_manifest())

    def test_connection_requires_instance_from_same_app(self):
        manifest = valid_manifest()
        app = manifest["apps"]["redis"]
        app["connection"] = "connection.json.mako"

        with self.assertRaisesRegex(ValueError, "connection_instance is required"):
            validate_manifest(manifest)

        app["connection_instance"] = "missing"
        with self.assertRaisesRegex(ValueError, "outside app"):
            validate_manifest(manifest)

        app["connection_instance"] = "redis-1"
        validate_manifest(manifest)

    def test_connection_path_is_normalized_relative_to_app_manifest(self):
        with TemporaryDirectory() as directory:
            data = {"connection": "connection.json.mako"}
            base_dir = Path(directory)
            _normalize_template_hook_and_connection_paths(data, base_dir)
            self.assertEqual(data["connection"], str((base_dir / "connection.json.mako").resolve()))


class ConnectionRenderTests(unittest.TestCase):
    def build_stand_and_cluster(self, template_text: str) -> tuple[Stand, ClusterApp]:
        role = RoleApp(name="server", ports=[])
        app = App(name="redis-1", role=role, cpu=500, ram=512)
        cluster = ClusterApp(
            name="redis",
            image=None,
            preferences={"user": "admin", "password": 's"ecret'},
            instances_app=[app],
            connection_template=Path("connection.json.mako"),
            connection_instance_name="redis-1",
        )
        stand = object.__new__(Stand)
        stand._connection_templates = {"redis": Template(template_text)}
        stand.instance_apps = {
            "redis-1": InstanceApp(
                app=app,
                cluster=cluster,
                node=SimpleNamespace(private_ip="10.0.0.2", public_ip="203.0.113.2"),
            )
        }
        return stand, cluster

    def test_renders_and_validates_connection_json(self):
        template = """<%! import json %>{
          \"endpoint\": ${json.dumps(node.private_ip)},
          \"port\": 6379,
          \"credentials\": {
            \"user\": ${json.dumps(cluster.preferences.user)},
            \"password\": ${json.dumps(cluster.preferences.password)},
            \"database\": 0
          }
        }"""
        stand, cluster = self.build_stand_and_cluster(template)
        connection = stand.render_connection(cluster)
        self.assertEqual(connection["endpoint"], "10.0.0.2")
        self.assertEqual(connection["credentials"]["password"], 's"ecret')
        self.assertEqual(connection["credentials"]["database"], 0)

    def test_rejects_invalid_contract(self):
        stand, cluster = self.build_stand_and_cluster(
            '{"endpoint": "10.0.0.2", "port": 70000, '
            '"credentials": {"user": "admin", "password": "secret"}}'
        )
        with self.assertRaisesRegex(ValueError, "between 1 and 65535"):
            stand.render_connection(cluster)


class ConnectionOutputTests(unittest.TestCase):
    def test_output_defaults_and_file_path_validation(self):
        defaults = OutputSettings()
        self.assertTrue(defaults.console)
        self.assertFalse(defaults.console_secrets)
        self.assertFalse(defaults.file)
        self.assertIsNone(defaults.file_path)

        with self.assertRaisesRegex(ValueError, "OUTPUT__FILE_PATH"):
            OutputSettings(file=True)

    def test_console_is_masked_and_file_contains_full_result(self):
        connections = {
            "redis": {
                "endpoint": "10.0.0.2",
                "port": 6379,
                "credentials": {
                    "user": "admin",
                    "password": "secret",
                    "database": 0,
                },
                "url": "redis://admin:secret@10.0.0.2:6379",
            }
        }

        with TemporaryDirectory() as directory:
            output_directory = Path(directory) / "nested"
            output_path = output_directory / "owner_demo_test.json"
            stand = object.__new__(Stand)
            stand.output_console = True
            stand.output_console_secrets = False
            stand.output_file = True
            stand.output_file_directory = output_directory
            stand.state = SimpleNamespace(owner="owner", project="demo", env="test")
            stand.build_connections = lambda: connections

            console = StringIO()
            with redirect_stdout(console):
                stand.output_connections()

            console_data = json.loads(console.getvalue())
            self.assertEqual(console_data["redis"]["credentials"]["user"], "admin")
            self.assertEqual(console_data["redis"]["credentials"]["password"], "***")
            self.assertEqual(console_data["redis"]["url"], "***")
            self.assertEqual(json.loads(output_path.read_text()), connections)
            self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o600)

    def test_existing_file_cannot_be_used_as_output_directory(self):
        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "connections.json"
            output_path.write_text("existing")

            with self.assertRaisesRegex(ValueError, "must point to a directory"):
                OutputSettings(file=True, file_path=output_path)

            settings = OutputSettings(file=False, file_path=output_path)
            self.assertFalse(settings.file)


if __name__ == "__main__":
    unittest.main()
