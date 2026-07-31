from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ManifestParser import parse_manifest
from StandBuilder import _build_cluster, _build_registries


STAND_MANIFEST = """
stand:
  project: demo
  env: test
  users: {sudo: admin, app: app}
  ssh: {key_name_admin: admin-key}
node_profiles:
  default:
    location: hel1
    type_serv: cpx11
    image: rocky-10
    network: demo
    cloud-init: cloud-init.yml.mako
registries:
  local: {url: registry.example.test}
apps:
  mongo:
    from_dep_manifest: app/app.yml
    instances:
      mongo-1:
        role: member
        cpu: 500
        ram: 512
        hooks:
          path: hook
          assets:
            - source: resource://project/migrations/mongo
              dest: migration
nodes:
  node-1:
    apps: [mongo-1]
"""

APP_MANIFEST = """
name: mongo
image: {registry: local, path: mongo, version: "8"}
roles:
  member: {}
templates:
  pod:
    path: mongo.yml.mako
    dest: /home/app/mongo.yml
    owner: app
    mode: "644"
"""


class HookResourceTests(unittest.TestCase):
    def create_manifest_tree(self, root: Path) -> tuple[Path, Path]:
        app = root / "app"
        (app / "hook").mkdir(parents=True)
        (app / "app.yml").write_text(APP_MANIFEST, encoding="utf-8")
        (app / "hook" / "hook.sh.mako").write_text("#!/bin/sh\n", encoding="utf-8")
        manifest = root / "stand.yml"
        manifest.write_text(STAND_MANIFEST, encoding="utf-8")

        project = root / "external-project"
        (project / "migrations" / "mongo").mkdir(parents=True)
        return manifest, project

    def test_resource_uri_resolves_to_named_root(self):
        with TemporaryDirectory() as directory:
            manifest, project = self.create_manifest_tree(Path(directory))

            data = parse_manifest(manifest, resource_roots={"project": project})

            hooks = data["apps"]["mongo"]["instances"]["mongo-1"]["hooks"]
            self.assertEqual(hooks["path"], str((manifest.parent / "app" / "hook").resolve()))
            self.assertEqual(
                hooks["assets"][0]["source"],
                str((project / "migrations" / "mongo").resolve()),
            )
            cluster, instances = _build_cluster(
                "mongo",
                data["apps"]["mongo"],
                _build_registries(data["registries"]),
            )
            self.assertEqual(cluster.instances_app[0], instances["mongo-1"])
            self.assertEqual(
                instances["mongo-1"].hook_assets[0].source,
                (project / "migrations" / "mongo").resolve(),
            )

    def test_create_rejects_unknown_resource(self):
        with TemporaryDirectory() as directory:
            manifest, _ = self.create_manifest_tree(Path(directory))

            with self.assertRaisesRegex(ValueError, "unknown resource 'project'"):
                parse_manifest(manifest)

    def test_relative_asset_source_is_relative_to_stand_manifest(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = self.create_manifest_tree(root)
            relative_source = root / "stand-assets" / "migrations"
            relative_source.mkdir(parents=True)
            manifest.write_text(
                STAND_MANIFEST.replace(
                    "resource://project/migrations/mongo",
                    "stand-assets/migrations",
                ),
                encoding="utf-8",
            )

            data = parse_manifest(manifest)

            source = data["apps"]["mongo"]["instances"]["mongo-1"]["hooks"]["assets"][0]["source"]
            self.assertEqual(source, str(relative_source.resolve()))

    def test_destroy_does_not_require_resource_checkout(self):
        with TemporaryDirectory() as directory:
            manifest, _ = self.create_manifest_tree(Path(directory))

            data = parse_manifest(manifest, operation="destroy")

            source = data["apps"]["mongo"]["instances"]["mongo-1"]["hooks"]["assets"][0]["source"]
            self.assertEqual(source, "resource://project/migrations/mongo")

    def test_resource_traversal_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, project = self.create_manifest_tree(root)
            manifest.write_text(
                STAND_MANIFEST.replace(
                    "resource://project/migrations/mongo",
                    "resource://project/../outside",
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "must not escape"):
                parse_manifest(manifest, resource_roots={"project": project})

    def test_resource_symlink_escape_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, project = self.create_manifest_tree(root)
            outside = root / "outside"
            outside.mkdir()
            (project / "escape").symlink_to(outside, target_is_directory=True)
            manifest.write_text(
                STAND_MANIFEST.replace(
                    "resource://project/migrations/mongo",
                    "resource://project/escape",
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "escapes its root"):
                parse_manifest(manifest, resource_roots={"project": project})

    def test_unsafe_destination_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, project = self.create_manifest_tree(root)
            manifest.write_text(
                STAND_MANIFEST.replace("dest: migration", "dest: ../migration"),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "relative POSIX path"):
                parse_manifest(manifest, resource_roots={"project": project})

    def test_multiple_assets_can_share_one_resource_root(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, project = self.create_manifest_tree(root)
            redpanda = project / "redpanda"
            redpanda.mkdir()
            manifest.write_text(
                STAND_MANIFEST.replace(
                    "              dest: migration",
                    "              dest: migration\n"
                    "            - source: resource://project/redpanda\n"
                    "              dest: .",
                ),
                encoding="utf-8",
            )

            data = parse_manifest(manifest, resource_roots={"project": project})

            assets = data["apps"]["mongo"]["instances"]["mongo-1"]["hooks"]["assets"]
            self.assertEqual(
                assets[0]["source"],
                str((project / "migrations" / "mongo").resolve()),
            )
            self.assertEqual(assets[1]["source"], str(redpanda.resolve()))
            self.assertEqual(assets[1]["dest"], ".")

    def test_demo_manifest_uses_one_shared_resource_root(self):
        repository = Path(__file__).parents[1]
        demo = repository / "demo"

        data = parse_manifest(
            demo / "stand" / "stand.yml",
            operation="destroy",
            resource_roots={"project-assets": demo / "resources"},
        )

        redpanda = data["apps"]["redpanda"]["instances"]["redpanda-master"]["hooks"]
        mongo = data["apps"]["mongo"]["instances"]["mongo-instance"]["hooks"]
        self.assertEqual(
            redpanda["assets"][0]["source"],
            str((demo / "resources" / "redpanda").resolve()),
        )
        self.assertEqual(redpanda["assets"][0]["dest"], ".")
        self.assertEqual(
            mongo["assets"][0]["source"],
            str((demo / "resources" / "mongo" / "migrations").resolve()),
        )


if __name__ == "__main__":
    unittest.main()
