from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from App import App, ClusterApp, HookAsset, RoleApp
from StandFramework import Stand
from StandFramework.stand.stand import InstanceApp


class HookAssetTests(unittest.TestCase):
    def build_stand(self, hook_path: Path, configset_path: Path):
        role = RoleApp(name="member", ports=[])
        app = App(
            name="mongo-1",
            role=role,
            cpu=500,
            ram=512,
            hook_path=hook_path,
        )
        cluster = ClusterApp(
            name="mongo",
            image=None,
            preferences={},
            instances_app=[app],
        )
        instance = InstanceApp(
            app=app,
            cluster=cluster,
            node=SimpleNamespace(private_ip="10.0.0.2"),
        )
        stand = object.__new__(Stand)
        stand.app_user = "app"
        stand.path_folder_configset = configset_path
        stand.instance_apps = {app.name: instance}
        stand.shell_script = []

        assets = []
        stand.add_upload_asset = lambda current_instance, asset: assets.append(asset)
        return stand, instance, assets

    def test_only_mako_files_are_rendered_and_renamed(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hook_path = root / "source"
            hook_path.mkdir()
            (hook_path / "hook.sh.mako").write_text(
                "echo ${instance.name}\n",
                encoding="utf-8",
            )
            (hook_path / "literal.txt").write_text(
                "${THIS_MUST_STAY_LITERAL}\n",
                encoding="utf-8",
            )
            (hook_path / "literal.MAKO").write_text(
                "${CASE_SENSITIVE_SUFFIX}\n",
                encoding="utf-8",
            )

            stand, instance, assets = self.build_stand(hook_path, root / "configset")
            stand.add_app_hook(instance)

            assets_by_dest = {asset.dest: asset.content for asset in assets}
            remote_root = "/home/app/hook/mongo-1"
            self.assertEqual(assets_by_dest[f"{remote_root}/hook.sh"], "echo mongo-1\n")
            self.assertEqual(
                assets_by_dest[f"{remote_root}/literal.txt"],
                b"${THIS_MUST_STAY_LITERAL}\n",
            )
            self.assertEqual(
                assets_by_dest[f"{remote_root}/literal.MAKO"],
                b"${CASE_SENSITIVE_SUFFIX}\n",
            )

            local_root = root / "configset" / "mongo--mongo-1" / "hook"
            self.assertEqual((local_root / "hook.sh").read_text(), "echo mongo-1\n")
            self.assertFalse((local_root / "hook.sh.mako").exists())
            self.assertEqual(
                (local_root / "literal.txt").read_bytes(),
                b"${THIS_MUST_STAY_LITERAL}\n",
            )

    def test_binary_files_and_nested_paths_are_preserved(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hook_path = root / "source"
            nested_path = hook_path / "migration"
            nested_path.mkdir(parents=True)
            (hook_path / "hook.sh.mako").write_text("#!/bin/sh\n", encoding="utf-8")
            binary_content = b"\x00\xff${not-a-template}\x80"
            (nested_path / "fixture.bin").write_bytes(binary_content)

            stand, instance, assets = self.build_stand(hook_path, root / "configset")
            stand.add_app_hook(instance)

            binary_asset = next(
                asset for asset in assets if asset.dest.endswith("/migration/fixture.bin")
            )
            self.assertEqual(binary_asset.content, binary_content)
            local_file = (
                root
                / "configset"
                / "mongo--mongo-1"
                / "hook"
                / "migration"
                / "fixture.bin"
            )
            self.assertEqual(local_file.read_bytes(), binary_content)

    def test_hook_sh_mako_remains_required(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hook_path = root / "source"
            hook_path.mkdir()
            (hook_path / "hook.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            stand, instance, _ = self.build_stand(hook_path, root / "configset")

            with self.assertRaisesRegex(Exception, "must contain hook.sh.mako"):
                stand.add_app_hook(instance)

    def test_external_asset_tree_is_copied_without_rendering(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hook_path = root / "hook"
            hook_path.mkdir()
            (hook_path / "hook.sh.mako").write_text("#!/bin/sh\n", encoding="utf-8")
            migrations = root / "project" / "migrations"
            migrations.mkdir(parents=True)
            (migrations / "001.js.mako").write_bytes(b"${MONGO_LITERAL}\x00")

            stand, instance, assets = self.build_stand(hook_path, root / "configset")
            instance.app.hook_assets = [
                HookAsset(source=migrations, dest=PurePosixPath("migration"))
            ]

            stand.add_app_hook(instance)

            asset = next(item for item in assets if item.dest.endswith("001.js.mako"))
            self.assertEqual(asset.content, b"${MONGO_LITERAL}\x00")
            self.assertEqual(
                (
                    root
                    / "configset"
                    / "mongo--mongo-1"
                    / "hook"
                    / "migration"
                    / "001.js.mako"
                ).read_bytes(),
                b"${MONGO_LITERAL}\x00",
            )

    def test_external_asset_cannot_overwrite_base_hook_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hook_path = root / "hook"
            (hook_path / "migration").mkdir(parents=True)
            (hook_path / "hook.sh.mako").write_text("#!/bin/sh\n", encoding="utf-8")
            (hook_path / "migration" / "001.js").write_text("base", encoding="utf-8")
            migrations = root / "project" / "migrations"
            migrations.mkdir(parents=True)
            (migrations / "001.js").write_text("external", encoding="utf-8")

            stand, instance, _ = self.build_stand(hook_path, root / "configset")
            instance.app.hook_assets = [
                HookAsset(source=migrations, dest=PurePosixPath("migration"))
            ]

            with self.assertRaisesRegex(ValueError, "Hook file collision"):
                stand.validate_hook_sources()

    def test_external_asset_can_be_added_to_hook_root(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hook_path = root / "hook"
            hook_path.mkdir()
            (hook_path / "hook.sh.mako").write_text("#!/bin/sh\n", encoding="utf-8")
            config = root / "project" / "redpanda"
            config.mkdir(parents=True)
            (config / "acl-map.sh").write_text("declare -A TOPICS=()\n", encoding="utf-8")

            stand, instance, assets = self.build_stand(hook_path, root / "configset")
            instance.app.hook_assets = [
                HookAsset(source=config, dest=PurePosixPath("."))
            ]

            stand.add_app_hook(instance)

            root_asset = next(item for item in assets if item.dest.endswith("acl-map.sh"))
            self.assertEqual(root_asset.dest, "/home/app/hook/mongo-1/acl-map.sh")

    def test_root_asset_cannot_overwrite_hook_script(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hook_path = root / "hook"
            hook_path.mkdir()
            (hook_path / "hook.sh.mako").write_text("#!/bin/sh\n", encoding="utf-8")
            asset = root / "asset"
            asset.mkdir()
            (asset / "hook.sh").write_text("external\n", encoding="utf-8")

            stand, instance, _ = self.build_stand(hook_path, root / "configset")
            instance.app.hook_assets = [
                HookAsset(source=asset, dest=PurePosixPath("."))
            ]

            with self.assertRaisesRegex(ValueError, "Hook file collision"):
                stand.validate_hook_sources()

    def test_missing_external_asset_is_rejected_during_preflight(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hook_path = root / "hook"
            hook_path.mkdir()
            (hook_path / "hook.sh.mako").write_text("#!/bin/sh\n", encoding="utf-8")
            stand, instance, _ = self.build_stand(hook_path, root / "configset")
            instance.app.hook_assets = [
                HookAsset(source=root / "missing", dest=PurePosixPath("migration"))
            ]

            with self.assertRaisesRegex(ValueError, "not a directory"):
                stand.validate_hook_sources()


if __name__ == "__main__":
    unittest.main()
