import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ManifestParser import SecretReference, parse_manifest, parse_yml, secret_env_name
import main


BASE_MANIFEST = """\
version: 1
stand:
  project: demo
  env: test
  users:
    sudo: admin
    app: app
  ssh:
    key_name_admin: admin-key
node_profiles:
  default:
    location: hel1
    type_serv: cpx32
    image: rocky-10
    network: demo-network
    cloud-init: cloud-init.yaml.mako
from_dep_manifest: registries.yml
apps:
  redis:
    name: redis
    image:
      registry: local
      path: redis
      version: "7"
    roles:
      server: {}
    templates:
      pod:
        path: redis.yml.mako
        dest: /home/app/redis.yml
        owner: app
        mode: "644"
    preferences:
      password: !secret app-password
    instances:
      redis-1:
        role: server
        cpu: 500
        ram: 512
nodes:
  node-1:
    apps: [redis-1]
"""

REGISTRY_MANIFEST = """\
version: 1
registries:
  local:
    url: registry.example.test
    username: robot
    password: !secret registry-password
"""


class ManifestSecretTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.manifest = self.root / "stand.yml"
        self.manifest.write_text(BASE_MANIFEST)
        (self.root / "registries.yml").write_text(REGISTRY_MANIFEST)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_secret_name_is_normalized(self):
        self.assertEqual(secret_env_name("my-password"), "SECRET_MY_PASSWORD")
        self.assertEqual(secret_env_name("Already_OK2"), "SECRET_ALREADY_OK2")

    def test_create_resolves_secrets_in_root_and_dependency(self):
        env = {
            "SECRET_APP_PASSWORD": "app-value",
            "SECRET_REGISTRY_PASSWORD": "registry-value",
        }
        with patch.dict(os.environ, env, clear=True):
            data = parse_manifest(self.manifest)

        self.assertEqual(data["apps"]["redis"]["preferences"]["password"], "app-value")
        self.assertEqual(data["registries"]["local"]["password"], "registry-value")

    def test_create_reports_all_missing_secrets_without_values(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "Missing or empty manifest secrets") as raised:
                parse_manifest(self.manifest)

        message = str(raised.exception)
        self.assertIn("SECRET_APP_PASSWORD", message)
        self.assertIn("manifest.apps.redis.preferences.password", message)
        self.assertIn("SECRET_REGISTRY_PASSWORD", message)
        self.assertIn("manifest.registries.local.password", message)
        self.assertNotIn("app-value", message)
        self.assertNotIn("registry-value", message)

    def test_empty_environment_value_is_missing(self):
        env = {
            "SECRET_APP_PASSWORD": "",
            "SECRET_REGISTRY_PASSWORD": "registry-value",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "SECRET_APP_PASSWORD"):
                parse_manifest(self.manifest)

    def test_destroy_allows_missing_application_and_registry_secrets(self):
        with patch.dict(os.environ, {}, clear=True):
            data = parse_manifest(self.manifest, operation="destroy")

        self.assertIsInstance(data["apps"]["redis"]["preferences"]["password"], SecretReference)
        self.assertIsInstance(data["registries"]["local"]["password"], SecretReference)

    def test_destroy_still_requires_structural_secrets(self):
        content = BASE_MANIFEST.replace("project: demo", "project: !secret project-name")
        self.manifest.write_text(content)

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "SECRET_PROJECT_NAME"):
                parse_manifest(self.manifest, operation="destroy")

    def test_secret_cannot_be_a_mapping_key(self):
        path = self.root / "invalid.yml"
        path.write_text("!secret key-name: value\n")

        with self.assertRaisesRegex(ValueError, "cannot be used as a mapping key"):
            parse_yml(path)

    def test_secret_must_be_a_scalar(self):
        path = self.root / "invalid.yml"
        path.write_text("value: !secret [name]\n")

        with self.assertRaisesRegex(ValueError, "must be applied to a scalar value"):
            parse_yml(path)

    def test_secret_name_must_be_valid(self):
        path = self.root / "invalid.yml"
        path.write_text("value: !secret 'bad name'\n")

        with self.assertRaisesRegex(ValueError, "must match"):
            parse_yml(path)

    def test_cli_stops_before_build_when_secret_preflight_fails(self):
        config = unittest.mock.Mock()
        config.stand.path_to_key = self.root / "missing-key"

        with (
            patch.object(main, "Config", return_value=config),
            patch.object(main, "parse_manifest", side_effect=ValueError("missing secret")),
            patch.object(main, "build_stand") as build_stand,
        ):
            exit_code = main.main(["main.py", "create", str(self.manifest)])

        self.assertEqual(exit_code, 1)
        build_stand.assert_not_called()


if __name__ == "__main__":
    unittest.main()
