from pathlib import Path
import re
from types import SimpleNamespace
import unittest

from mako.template import Template
import yaml

from App import App, ClusterApp, RoleApp
from ManifestParser.validation import validate_manifest
from ShellCollect import ImageRegistry, ShellCollect
from StandBuilder import _build_cluster


def valid_manifest() -> dict:
    return {
        "stand": {
            "project": "demo",
            "env": "test",
            "users": {"sudo": "admin", "app": "app"},
            "ssh": {"key_name_admin": "admin-key"},
        },
        "registries": {"local": {"url": "registry.example.test"}},
        "apps": {
            "redis": {
                "name": "redis",
                "image": {"registry": "local", "path": "redis", "version": "7"},
                "roles": {"server": {}},
                "templates": {
                    "pod": {
                        "path": "redis.yml.mako",
                        "dest": "/home/app/redis.yml",
                        "owner": "app",
                        "mode": "644",
                    }
                },
                "instances": {
                    "redis-1": {
                        "role": "server",
                        "cpu": 500,
                        "ram": 512,
                        "oom_priority": 0,
                    }
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


class AppResourceValidationTests(unittest.TestCase):
    def test_valid_resources_and_oom_boundaries(self):
        for oom_priority in (-1000, 0, 1000, None):
            with self.subTest(oom_priority=oom_priority):
                manifest = valid_manifest()
                instance = manifest["apps"]["redis"]["instances"]["redis-1"]
                if oom_priority is None:
                    instance.pop("oom_priority")
                else:
                    instance["oom_priority"] = oom_priority
                validate_manifest(manifest)

    def test_cpu_and_ram_are_required_positive_integers(self):
        for field, value, error in (
            ("cpu", None, "cpu is required"),
            ("ram", None, "ram is required"),
            ("cpu", 0, "cpu must be greater than zero"),
            ("ram", -1, "ram must be greater than zero"),
            ("cpu", True, "cpu must be an integer"),
            ("ram", 512.5, "ram must be an integer"),
        ):
            with self.subTest(field=field, value=value):
                manifest = valid_manifest()
                instance = manifest["apps"]["redis"]["instances"]["redis-1"]
                if value is None:
                    instance.pop(field)
                else:
                    instance[field] = value
                with self.assertRaisesRegex(ValueError, error):
                    validate_manifest(manifest)

    def test_oom_priority_is_a_bounded_integer(self):
        for value in (-1001, 1001, True, 1.5):
            with self.subTest(value=value):
                manifest = valid_manifest()
                manifest["apps"]["redis"]["instances"]["redis-1"]["oom_priority"] = value
                with self.assertRaises(ValueError):
                    validate_manifest(manifest)


class AppResourceBuildAndRenderTests(unittest.TestCase):
    def test_cluster_instance_count_tracks_instances(self):
        role = RoleApp(name="server", ports=[])
        cluster = ClusterApp(name="redis", image=None)

        self.assertEqual(cluster.instance_count, 0)

        cluster.instances_app.extend(
            [
                App(name="redis-1", role=role, cpu=500, ram=512),
                App(name="redis-2", role=role, cpu=500, ram=512),
            ]
        )

        self.assertEqual(cluster.instance_count, 2)

    def test_builder_populates_app_resources(self):
        manifest = valid_manifest()
        app_data = manifest["apps"]["redis"]
        registry = ImageRegistry(url="registry.example.test")

        _, instances = _build_cluster("redis", app_data, {"local": registry})

        instance = instances["redis-1"]
        self.assertEqual(instance.cpu, 500)
        self.assertEqual(instance.ram, 512)
        self.assertEqual(instance.oom_priority, 0)

    def test_redis_template_renders_resources_and_optional_annotation(self):
        template_path = (
            Path(__file__).parents[1]
            / "demo"
            / "app-registry"
            / "redis"
            / "redis-instance.yml.mako"
        )
        template = Template(filename=str(template_path))
        role = RoleApp(name="server", ports=[])
        cluster = SimpleNamespace(
            image=SimpleNamespace(full_name="registry.example.test/redis:7"),
            preferences=SimpleNamespace(admin_user="admin", admin_pass="secret"),
        )

        for oom_priority, annotation_expected in ((0, True), (None, False)):
            with self.subTest(oom_priority=oom_priority):
                instance = App(
                    name="redis-1",
                    role=role,
                    cpu=500,
                    ram=512,
                    oom_priority=oom_priority,
                )
                rendered = template.render(
                    node=SimpleNamespace(private_ip="10.0.0.2"),
                    instance=instance,
                    role=role,
                    cluster=cluster,
                    apps={},
                )
                documents = [document for document in yaml.safe_load_all(rendered) if document]
                pod = next(document for document in documents if document["kind"] == "Pod")
                resources = pod["spec"]["containers"][0]["resources"]

                self.assertEqual(resources["requests"], {"cpu": "500m", "memory": "512M"})
                self.assertEqual(resources["limits"], resources["requests"])
                annotations = pod["metadata"]["annotations"]
                self.assertEqual(
                    "io.podman.annotations.oom_score_adj" in annotations,
                    annotation_expected,
                )

    def test_all_demo_pod_templates_use_instance_resources(self):
        registry_path = Path(__file__).parents[1] / "demo" / "app-registry"
        template_paths = (
            registry_path / "redis" / "redis-instance.yml.mako",
            registry_path / "redpanda" / "redpanda-instance.yml.mako",
            registry_path / "mongo" / "mongo-instance.yml.mako",
            registry_path / "kafka-ui" / "kafka-ui.yml.mako",
            registry_path / "dozzle" / "dozzle-instance.yml.mako",
        )

        for template_path in template_paths:
            with self.subTest(template=template_path.name):
                content = template_path.read_text()
                self.assertEqual(content.count('cpu: "${instance.cpu}m"'), 2)
                self.assertEqual(content.count('memory: "${instance.ram}M"'), 2)
                self.assertIn("io.podman.annotations.oom_score_adj", content)
                self.assertIn('stands-engine.io/managed: "true"', content)

    def test_dozzle_template_mounts_podman_socket_and_persistent_data(self):
        template_path = (
            Path(__file__).parents[1]
            / "demo"
            / "app-registry"
            / "dozzle"
            / "dozzle-instance.yml.mako"
        )
        template = Template(filename=str(template_path))
        role = RoleApp(name="logs-viewer", ports=[])
        instance = App(
            name="dozzle",
            role=role,
            cpu=500,
            ram=512,
        )
        rendered = template.render(
            node=SimpleNamespace(private_ip="10.0.0.2"),
            instance=instance,
            role=role,
            cluster=SimpleNamespace(
                image=SimpleNamespace(full_name="registry.example.test/dozzle:v10.6.11"),
            ),
            apps={},
        )
        pod = yaml.safe_load(rendered)
        container = pod["spec"]["containers"][0]

        self.assertEqual(
            pod["metadata"]["labels"],
            {"stands-engine.io/managed": "true"},
        )
        self.assertEqual(
            container["ports"][0],
            {"containerPort": 8080, "hostPort": 3000, "hostIP": "10.0.0.2"},
        )
        self.assertEqual(
            container["env"],
            [
                {
                    "name": "DOZZLE_FILTER",
                    "value": "label=stands-engine.io/managed=true",
                }
            ],
        )
        self.assertEqual(
            container["volumeMounts"],
            [
                {
                    "name": "podman-socket",
                    "mountPath": "/var/run/docker.sock",
                    "readOnly": True,
                },
                {"name": "dozzle-data", "mountPath": "/data"},
            ],
        )
        self.assertEqual(
            pod["spec"]["volumes"],
            [
                {
                    "name": "podman-socket",
                    "hostPath": {"path": "/home/userapp/podman.sock"},
                },
                {
                    "name": "dozzle-data",
                    "persistentVolumeClaim": {"claimName": "dozzle-data"},
                },
            ],
        )

    def test_dozzle_technical_container_name_filter_covers_quadlet_helpers(self):
        technical_name_pattern = r".*-(infra|service)$"

        self.assertRegex("a1b2c3d4e5f6-infra", technical_name_pattern)
        self.assertRegex("a1b2c3d4e5f6-service", technical_name_pattern)
        self.assertIsNone(re.fullmatch(technical_name_pattern, "redpanda-master"))


class PodmanRuntimeSetupTests(unittest.TestCase):
    def test_runtime_enables_socket_and_prepares_dozzle_compatibility(self):
        commands = ShellCollect.setting_podman_app_runtime("userapp", "podman")
        commands_by_name = {command.name: command.cmd for command in commands}

        socket_name = "Enable user Podman socket for userapp"
        stable_path_name = "Create stable Podman socket path for userapp"
        engine_id_name = "Ensure Podman engine ID for Docker API clients"

        self.assertIn("enable --now podman.socket", commands_by_name[socket_name])
        self.assertIn(
            "/run/user/$(id -u userapp)/podman/podman.sock",
            commands_by_name[stable_path_name],
        )
        self.assertIn("/home/userapp/podman.sock", commands_by_name[stable_path_name])
        self.assertIn("test -s /var/lib/docker/engine-id", commands_by_name[engine_id_name])
        self.assertLess(
            [command.name for command in commands].index(socket_name),
            [command.name for command in commands].index(stable_path_name),
        )


if __name__ == "__main__":
    unittest.main()
