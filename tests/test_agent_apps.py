from copy import deepcopy
import unittest

from ManifestParser import SecretReference
from ManifestParser.validation import validate_manifest
from StandBuilder import _build_cluster, _build_nodes, _build_registries, _expand_agent_apps


def valid_manifest() -> dict:
    template = {
        "path": "app.yml.mako",
        "dest": "/home/app/app.yml",
        "owner": "app",
        "mode": "644",
    }
    return {
        "stand": {
            "project": "demo",
            "env": "test",
            "users": {"sudo": "admin", "app": "app"},
            "ssh": {"key_name_admin": "admin-key"},
        },
        "registries": {"local": {"url": "registry.example.test"}},
        "apps": {
            "service": {
                "name": "service",
                "image": {"registry": "local", "path": "service", "version": "1"},
                "roles": {"server": {}},
                "templates": {"pod": dict(template)},
                "instances": {
                    "service-1": {"role": "server", "cpu": 500, "ram": 512},
                },
            },
            "agent": {
                "name": "agent",
                "image": {"registry": "local", "path": "agent", "version": "2"},
                "roles": {"collector": {}},
                "templates": {"pod": dict(template)},
                "instances": {
                    "agent": {
                        "role": "collector",
                        "cpu": 250,
                        "ram": 128,
                        "oom_priority": 100,
                        "hooks": "hooks",
                        "preferences": {"level": "info"},
                    },
                },
            },
        },
        "agents": {"apps": ["agent"]},
        "node_profiles": {
            "default": {
                "location": "hel1",
                "type_serv": "cpx32",
                "image": "rocky-10",
                "network": "demo-network",
                "cloud-init": "cloud-init.yaml.mako",
            },
        },
        "nodes": {
            "node-1": {"apps": ["service-1"]},
            "node-2": {"apps": []},
        },
    }


class AgentAppsValidationTests(unittest.TestCase):
    def test_agents_are_optional_and_empty_list_is_allowed(self):
        for agents in (None, {"apps": []}):
            with self.subTest(agents=agents):
                manifest = valid_manifest()
                manifest["nodes"]["node-1"]["apps"].append("agent")
                if agents is None:
                    manifest.pop("agents")
                else:
                    manifest["agents"] = agents
                validate_manifest(manifest)

    def test_agent_apps_must_be_a_list_of_known_unique_instances(self):
        cases = (
            ({}, "manifest.agents.apps is required"),
            ({"apps": "agent"}, "manifest.agents.apps must be a list"),
            ({"apps": [""]}, "manifest.agents.apps\\[0\\] must be a non-empty string"),
            ({"apps": ["missing"]}, "references unknown instance 'missing'"),
            ({"apps": ["agent", "agent"]}, "duplicates agent instance 'agent'"),
        )
        for agents, error in cases:
            with self.subTest(agents=agents):
                manifest = valid_manifest()
                manifest["agents"] = agents
                with self.assertRaisesRegex(ValueError, error):
                    validate_manifest(manifest)

        for agents in (None, []):
            with self.subTest(agents=agents):
                manifest = valid_manifest()
                manifest["agents"] = agents
                with self.assertRaisesRegex(ValueError, "manifest.agents must be a mapping"):
                    validate_manifest(manifest)

    def test_agent_cannot_also_be_assigned_to_a_node(self):
        manifest = valid_manifest()
        manifest["nodes"]["node-1"]["apps"].append("agent")

        with self.assertRaisesRegex(ValueError, "agent instance 'agent' cannot be assigned"):
            validate_manifest(manifest)

    def test_agent_cannot_be_a_connection_instance(self):
        manifest = valid_manifest()
        manifest["apps"]["agent"]["connection"] = "connection.json.mako"
        manifest["apps"]["agent"]["connection_instance"] = "agent"

        with self.assertRaisesRegex(ValueError, "connection_instance cannot reference agent instance"):
            validate_manifest(manifest)

    def test_generated_name_cannot_conflict_with_a_declared_instance(self):
        manifest = valid_manifest()
        manifest["apps"]["service"]["instances"]["agent--node-1"] = {
            "role": "server",
            "cpu": 100,
            "ram": 128,
        }

        with self.assertRaisesRegex(ValueError, "conflicts with a declared instance"):
            validate_manifest(manifest)


class AgentAppsBuildTests(unittest.TestCase):
    def test_expansion_preserves_unresolved_destroy_secrets(self):
        manifest = valid_manifest()
        secret = SecretReference("agent-token", "test manifest")
        manifest["apps"]["agent"]["instances"]["agent"]["preferences"]["token"] = secret

        expanded = _expand_agent_apps(manifest)

        self.assertIs(
            expanded["apps"]["agent"]["instances"]["agent--node-1"]["preferences"]["token"],
            secret,
        )

    def test_builder_expands_agents_to_the_existing_instance_model(self):
        manifest = valid_manifest()
        original = deepcopy(manifest)
        validate_manifest(manifest)

        expanded = _expand_agent_apps(manifest)

        self.assertEqual(manifest, original)
        self.assertNotIn("agents", expanded)
        self.assertEqual(
            list(expanded["apps"]["agent"]["instances"]),
            ["agent--node-1", "agent--node-2"],
        )
        self.assertEqual(
            expanded["nodes"]["node-1"]["apps"],
            ["service-1", "agent--node-1"],
        )
        self.assertEqual(expanded["nodes"]["node-2"]["apps"], ["agent--node-2"])

        registries = _build_registries(expanded["registries"])
        instances_by_name = {}
        clusters = {}
        for app_name, app_data in expanded["apps"].items():
            cluster, instances = _build_cluster(app_name, app_data, registries)
            clusters[app_name] = cluster
            instances_by_name.update(instances)

        nodes = _build_nodes(expanded["nodes"], expanded["node_profiles"], instances_by_name)
        first_agent = instances_by_name["agent--node-1"]
        second_agent = instances_by_name["agent--node-2"]

        self.assertIsNot(first_agent, second_agent)
        self.assertEqual(first_agent.role.name, "collector")
        self.assertEqual(first_agent.cpu, 250)
        self.assertEqual(first_agent.ram, 128)
        self.assertEqual(first_agent.oom_priority, 100)
        self.assertEqual(first_agent.preferences, {"level": "info"})
        self.assertEqual(str(first_agent.hook_path), "hooks")
        self.assertEqual(
            [instance.name for instance in nodes["node-1"].instances_app],
            ["service-1", "agent--node-1"],
        )
        self.assertEqual(
            [instance.name for instance in nodes["node-2"].instances_app],
            ["agent--node-2"],
        )
        self.assertEqual(
            [instance.name for instance in clusters["agent"].instances_app],
            ["agent--node-1", "agent--node-2"],
        )


if __name__ == "__main__":
    unittest.main()
