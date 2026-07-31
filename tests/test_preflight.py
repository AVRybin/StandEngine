from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from StandBuilder import build_stand
from StandFramework.preflight import PreflightValidationError


POD_TEMPLATE = """apiVersion: v1
kind: Pod
metadata:
  name: ${instance.name}
spec:
  containers:
    - name: ${instance.name}
      image: ${cluster.image.full_name}
      ports:
        - containerPort: 8080
          hostPort: 8080
          hostIP: ${node.private_ip}
"""

CLOUD_INIT_TEMPLATE = """#cloud-config
users:
  - name: ${user_admin}
network_range: ${network_ip_range}
"""

CONNECTION_TEMPLATE = """<%! import json %>{
  "endpoint": ${json.dumps(node.private_ip)},
  "port": 8080,
  "credentials": {"user": "admin", "password": ${json.dumps(cluster.preferences.password)}}
}
"""


class PreflightTests(unittest.TestCase):
    def make_stand(self, root: Path, *, second_instance: bool = False):
        cloud_init = root / "cloud-init.yml.mako"
        pod = root / "pod.yml.mako"
        connection = root / "connection.json.mako"
        cloud_init.write_text(CLOUD_INIT_TEMPLATE, encoding="utf-8")
        pod.write_text(POD_TEMPLATE, encoding="utf-8")
        connection.write_text(CONNECTION_TEMPLATE, encoding="utf-8")

        instances = {
            "web-1": {
                "role": "web",
                "cpu": 100,
                "ram": 128,
                "preferences": {},
            }
        }
        node_apps = ["web-1"]
        if second_instance:
            instances["web-2"] = {
                "role": "web",
                "cpu": 100,
                "ram": 128,
                "preferences": {},
            }
            node_apps.append("web-2")

        data = {
            "stand": {
                "project": "demo",
                "env": "test",
                "users": {"sudo": "admin", "app": "app"},
                "ssh": {"key_name_admin": "admin-key"},
            },
            "registries": {"local": {"url": "registry.example.test"}},
            "apps": {
                "web": {
                    "name": "web",
                    "image": {"registry": "local", "path": "web", "version": "1"},
                    "roles": {"web": {"ports": []}},
                    "templates": {
                        "pod": {
                            "path": str(pod),
                            "dest": "/home/app/web.yml",
                            "owner": "app",
                            "mode": "644",
                        }
                    },
                    "connection": str(connection),
                    "connection_instance": "web-1",
                    "preferences": {"password": "top-secret-value"},
                    "instances": instances,
                }
            },
            "node_profiles": {
                "default": {
                    "location": "hel1",
                    "type_serv": "cpx11",
                    "image": "rocky-10",
                    "network": "test-network",
                    "cloud-init": str(cloud_init),
                }
            },
            "nodes": {"node-1": {"apps": node_apps}},
        }
        config = SimpleNamespace(
            stand=SimpleNamespace(
                user="owner",
                passphrase="unused-by-local-validation",
                path_to_key=root / "key",
                path_to_configset=root / "configsets",
            ),
            output=SimpleNamespace(
                console=True,
                console_secrets=False,
                file=False,
                file_path=None,
            ),
        )
        return build_stand(data, config)

    def test_success_does_not_initialize_cloud_backend_or_leave_placeholder_ips(self):
        with TemporaryDirectory() as directory:
            stand = self.make_stand(Path(directory))

            stand.validate_preflight()

            self.assertIsNone(stand.backend)
            self.assertIsNone(stand.provision)
            self.assertTrue(stand._preflight_validated)
            self.assertFalse(hasattr(stand.nodes["node-1"], "private_ip"))

    def test_collects_independent_template_errors_and_redacts_preferences(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            stand = self.make_stand(root)
            (root / "cloud-init.yml.mako").write_text("users: [\n", encoding="utf-8")
            (root / "pod.yml.mako").write_text("${cluster.preferences.password}\n${missing}\n", encoding="utf-8")
            (root / "connection.json.mako").write_text("not-json ${cluster.preferences.password}", encoding="utf-8")

            with self.assertRaises(PreflightValidationError) as raised:
                stand.validate_preflight()

            message = str(raised.exception)
            self.assertGreaterEqual(len(raised.exception.issues), 3)
            self.assertIn("cloud-init", message)
            self.assertIn("template 'pod'", message)
            self.assertIn("connection", message)
            self.assertNotIn("top-secret-value", message)

    def test_detects_host_port_conflict_on_same_node(self):
        with TemporaryDirectory() as directory:
            stand = self.make_stand(Path(directory), second_instance=True)

            with self.assertRaisesRegex(PreflightValidationError, "hostPort 8080/TCP conflicts"):
                stand.validate_preflight()

    def test_rejects_upload_metadata_before_archive_build(self):
        with TemporaryDirectory() as directory:
            stand = self.make_stand(Path(directory))
            config = stand.clusters_app["web"].paths_to_templates["pod"]
            config.mode = "99"
            config.dest = "/etc/web.yml"

            with self.assertRaises(PreflightValidationError) as raised:
                stand.validate_preflight()

            message = str(raised.exception)
            self.assertIn("octal", message)
            self.assertIn("must be under /home/app", message)


if __name__ == "__main__":
    unittest.main()
