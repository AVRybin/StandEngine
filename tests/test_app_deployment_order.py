from pathlib import Path
from types import SimpleNamespace
import unittest

from StandBuilder import build_stand


class AppDeploymentOrderTests(unittest.TestCase):
    def test_launch_order_follows_apps_then_instances_not_node_placement(self):
        template = {
            "path": "pod.yml.mako",
            "dest": "/home/app/pod.yml",
            "owner": "app",
            "mode": "644",
        }
        manifest = {
            "stand": {
                "project": "demo",
                "env": "test",
                "users": {"sudo": "admin", "app": "app"},
                "ssh": {"key_name_admin": "admin-key"},
            },
            "registries": {"local": {"url": "registry.example.test"}},
            "apps": {
                "database": {
                    "name": "database",
                    "image": {"registry": "local", "path": "database", "version": "1"},
                    "roles": {"server": {}},
                    "templates": {"pod": dict(template)},
                    "instances": {
                        "database-2": {"role": "server", "cpu": 500, "ram": 512},
                        "database-1": {"role": "server", "cpu": 500, "ram": 512},
                    },
                },
                "frontend": {
                    "name": "frontend",
                    "image": {"registry": "local", "path": "frontend", "version": "1"},
                    "roles": {"web": {}},
                    "templates": {"pod": dict(template)},
                    "instances": {
                        "frontend-1": {"role": "web", "cpu": 250, "ram": 256},
                    },
                },
            },
            "node_profiles": {
                "default": {
                    "location": "hel1",
                    "type_serv": "cpx11",
                    "image": "rocky-10",
                    "network": "test-network",
                    "cloud-init": "cloud-init.yml.mako",
                }
            },
            "nodes": {
                "node-1": {
                    "apps": ["frontend-1", "database-1", "database-2"],
                }
            },
        }
        config = SimpleNamespace(
            stand=SimpleNamespace(
                user="owner",
                passphrase="unused",
                path_to_configset=Path("configsets"),
            ),
            output=SimpleNamespace(
                console=False,
                console_secrets=False,
                file=False,
                file_path=None,
            ),
        )
        stand = build_stand(manifest, config)

        self.assertEqual(
            list(stand.instance_apps),
            ["database-2", "database-1", "frontend-1"],
        )

        stand.add_app_hook = lambda instance: stand.shell_script.append(
            SimpleNamespace(name=f"Run hook {instance.app.name}")
        )
        stand.launch_apps()

        self.assertEqual(
            [operation.name for operation in stand.shell_script],
            [
                "Generate Podman unit database-2",
                "Start user container unit database-2",
                "Wait user service database-2 active",
                "Run hook database-2",
                "Generate Podman unit database-1",
                "Start user container unit database-1",
                "Wait user service database-1 active",
                "Run hook database-1",
                "Generate Podman unit frontend-1",
                "Start user container unit frontend-1",
                "Wait user service frontend-1 active",
                "Run hook frontend-1",
            ],
        )


if __name__ == "__main__":
    unittest.main()
