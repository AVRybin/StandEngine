import os
from pathlib import Path
import stat
import subprocess
from tempfile import TemporaryDirectory
import unittest


LAUNCHER = Path(__file__).parents[1] / "stands-engine"


class ContainerLauncherTests(unittest.TestCase):
    def test_named_resource_is_mounted_and_forwarded(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "demo" / "stand" / "stand.yml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("stand: {}\n", encoding="utf-8")
            resource = root / "demo" / "resources"
            resource.mkdir(parents=True)
            binary = root / "bin"
            binary.mkdir()
            docker = binary / "docker"
            docker.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" >\"$DOCKER_ARGS\"\n",
                encoding="utf-8",
            )
            docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
            arguments = root / "docker-args"

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


if __name__ == "__main__":
    unittest.main()
