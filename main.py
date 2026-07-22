import argparse
from importlib.metadata import PackageNotFoundError, version
import sys
from pathlib import Path

from config.config import Config
from ManifestParser import parse_manifest
from StandBuilder import build_stand


def application_version() -> str:
    try:
        return version("stands-engine")
    except PackageNotFoundError:
        return "0.1.0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stands-engine",
        description="Create or destroy an infrastructure stand from a YAML manifest.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {application_version()}")
    parser.add_argument("operation", choices=("create", "destroy"))
    parser.add_argument("manifest", type=Path, help="path to the stand YAML manifest")
    return parser


def parse_args(argv: list[str]) -> tuple[bool, Path]:
    args = build_parser().parse_args(argv[1:])

    return args.operation == "destroy", args.manifest


def load_private_key(path_to_key: Path) -> str:
    if not path_to_key.exists():
        return ""

    with open(path_to_key, "r") as f:
        return f.read()


def main(argv: list[str]) -> int:
    is_destroy, path_to_stand_manifest = parse_args(argv)

    try:
        config = Config()
        path_to_key = config.stand.path_to_key
        operation = "destroy" if is_destroy else "create"
        stand_data = parse_manifest(path_to_stand_manifest, operation=operation)
        stand = build_stand(stand_data, config, private_key=load_private_key(path_to_key))
    except (FileNotFoundError, TypeError, ValueError) as exc:
        print(exc)
        return 1

    if is_destroy:
        stand.destroy()
        return 0

    if not path_to_key.exists():
        with open(path_to_key, "w") as f:
            f.write(stand.key.private)

    try:
        stand.up(diagnostic=True)
    except (FileNotFoundError, TypeError, ValueError) as exc:
        print(exc)
        return 1
    return 0


def cli() -> int:
    return main(sys.argv)


if __name__ == "__main__":
    sys.exit(cli())
