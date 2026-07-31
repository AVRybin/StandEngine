import os
from pathlib import Path
import shutil
import stat
import subprocess
from tempfile import TemporaryDirectory
import unittest


LAUNCHER = Path(__file__).parents[1] / "stands-engine"


class ContainerLauncherTests(unittest.TestCase):
    @staticmethod
    def write_fake_runtime(root: Path) -> tuple[Path, Path]:
        binary = root / "bin"
        binary.mkdir()
        docker = binary / "docker"
        docker.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" >\"$DOCKER_ARGS\"\n",
            encoding="utf-8",
        )
        docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
        return binary, root / "docker-args"

    def test_named_resource_is_mounted_and_forwarded(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "demo" / "stand" / "stand.yml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("stand: {}\n", encoding="utf-8")
            resource = root / "demo" / "resources"
            resource.mkdir(parents=True)
            binary, arguments = self.write_fake_runtime(root)

            result = subprocess.run(
                [
                    "bash",
                    str(LAUNCHER),
                    "--runtime",
                    "docker",
                    "--resource",
                    f"project-assets={resource}",
                    "create",
                    str(manifest),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                env={
                    **os.environ,
                    "PATH": f"{binary}:{os.environ['PATH']}",
                    "DOCKER_ARGS": str(arguments),
                },
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            argv = arguments.read_text(encoding="utf-8").splitlines()
            self.assertIn(
                f"{resource.resolve()}:/resources/project-assets:ro",
                argv,
            )
            resource_index = argv.index("--resource")
            self.assertEqual(
                argv[resource_index + 1],
                "project-assets=/resources/project-assets",
            )
            self.assertEqual(argv[-2:], ["create", "/workspace/demo/stand/stand.yml"])

    def test_multiple_env_files_are_forwarded_in_order(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "stand.yml"
            manifest.write_text("stand: {}\n", encoding="utf-8")
            common_env = root / "common.env"
            common_env.write_text("VALUE=common\n", encoding="utf-8")
            stand_env = root / "stand settings.env"
            stand_env.write_text("VALUE=stand\n", encoding="utf-8")
            binary, arguments = self.write_fake_runtime(root)

            result = subprocess.run(
                [
                    "bash",
                    str(LAUNCHER),
                    "--runtime",
                    "docker",
                    "--env-file",
                    str(common_env),
                    "--env-file",
                    str(stand_env),
                    "create",
                    str(manifest),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                env={
                    **os.environ,
                    "PATH": f"{binary}:{os.environ['PATH']}",
                    "DOCKER_ARGS": str(arguments),
                },
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            argv = arguments.read_text(encoding="utf-8").splitlines()
            env_file_indices = [
                index for index, argument in enumerate(argv) if argument == "--env-file"
            ]
            self.assertEqual(len(env_file_indices), 2)
            self.assertEqual(
                [argv[index + 1] for index in env_file_indices],
                [str(common_env.resolve()), str(stand_env.resolve())],
            )
            first_managed_env = argv.index("--env")
            self.assertGreater(first_managed_env, env_file_indices[-1])

    def test_single_env_file_remains_supported(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "stand.yml"
            manifest.write_text("stand: {}\n", encoding="utf-8")
            env_file = root / "stand.env"
            env_file.write_text("VALUE=stand\n", encoding="utf-8")
            binary, arguments = self.write_fake_runtime(root)

            result = subprocess.run(
                [
                    "bash",
                    str(LAUNCHER),
                    "--runtime",
                    "docker",
                    "--env-file",
                    str(env_file),
                    "create",
                    str(manifest),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                env={
                    **os.environ,
                    "PATH": f"{binary}:{os.environ['PATH']}",
                    "DOCKER_ARGS": str(arguments),
                },
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            argv = arguments.read_text(encoding="utf-8").splitlines()
            env_file_index = argv.index("--env-file")
            self.assertEqual(argv[env_file_index + 1], str(env_file.resolve()))
            self.assertEqual(argv.count("--env-file"), 1)

    def test_missing_env_file_fails_before_runtime_is_started(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "stand.yml"
            manifest.write_text("stand: {}\n", encoding="utf-8")
            existing_env = root / "common.env"
            existing_env.write_text("VALUE=common\n", encoding="utf-8")
            missing_env = root / "missing.env"
            binary, arguments = self.write_fake_runtime(root)

            result = subprocess.run(
                [
                    "bash",
                    str(LAUNCHER),
                    "--runtime",
                    "docker",
                    "--env-file",
                    str(existing_env),
                    "--env-file",
                    str(missing_env),
                    "create",
                    str(manifest),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                env={
                    **os.environ,
                    "PATH": f"{binary}:{os.environ['PATH']}",
                    "DOCKER_ARGS": str(arguments),
                },
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn(f"Environment file does not exist: {missing_env}", result.stderr)
            self.assertFalse(arguments.exists())

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh is not installed")
    def test_powershell_multiple_env_files_are_forwarded_in_order(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "stand.yml"
            manifest.write_text("stand: {}\n", encoding="utf-8")
            common_env = root / "common.env"
            common_env.write_text("VALUE=common\n", encoding="utf-8")
            stand_env = root / "stand.env"
            stand_env.write_text("VALUE=stand\n", encoding="utf-8")
            binary, arguments = self.write_fake_runtime(root)
            launcher = LAUNCHER.with_suffix(".ps1")

            def ps_quote(path: Path) -> str:
                return str(path).replace("'", "''")

            command = (
                f"& '{ps_quote(launcher)}' create '{ps_quote(manifest)}' "
                f"-Runtime docker -EnvFile @('{ps_quote(common_env)}',"
                f"'{ps_quote(stand_env)}')"
            )
            result = subprocess.run(
                ["pwsh", "-NoProfile", "-Command", command],
                cwd=root,
                text=True,
                capture_output=True,
                env={
                    **os.environ,
                    "PATH": f"{binary}:{os.environ['PATH']}",
                    "DOCKER_ARGS": str(arguments),
                },
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            argv = arguments.read_text(encoding="utf-8").splitlines()
            env_file_indices = [
                index for index, argument in enumerate(argv) if argument == "--env-file"
            ]
            self.assertEqual(
                [argv[index + 1] for index in env_file_indices],
                [str(common_env.resolve()), str(stand_env.resolve())],
            )


if __name__ == "__main__":
    unittest.main()
