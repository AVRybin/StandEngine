import argparse
from importlib.metadata import PackageNotFoundError, version
import sys
from pathlib import Path
import re

from config.config import Config
from ManifestParser import parse_manifest
from StandBuilder import build_stand


RESOURCE_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def application_version() -> str:
    try:
        return version("stands-engine")
    except PackageNotFoundError:
        return "0.1.1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stands-engine",
        description="Create or destroy an infrastructure stand from a YAML manifest.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {application_version()}")
    parser.add_argument(
        "--resource",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="named local directory available to hook assets (repeatable)",
    )
    parser.add_argument("operation", choices=("create", "destroy"))
    parser.add_argument("manifest", type=Path, help="path to the stand YAML manifest")
    return parser


def parse_args(argv: list[str]) -> tuple[bool, Path]:
    args = build_parser().parse_args(argv[1:])

    return args.operation == "destroy", args.manifest


def parse_resource_roots(values: list[str]) -> dict[str, Path]:
    resources = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not RESOURCE_NAME_PATTERN.fullmatch(name) or not raw_path:
            raise ValueError(
                f"Invalid resource {value!r}; expected NAME=PATH with a valid name"
            )
        if name in resources:
            raise ValueError(f"Resource {name!r} was specified more than once")

        path = Path(raw_path).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"Resource {name!r} is not a directory: {path}")
        resources[name] = path
    return resources


def load_private_key(path_to_key: Path) -> str:
    if not path_to_key.exists():
        return ""

    with open(path_to_key, "r") as f:
        return f.read()


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv[1:])
    is_destroy = args.operation == "destroy"
    path_to_stand_manifest = args.manifest

    try:
        resource_roots = parse_resource_roots(args.resource)
        config = Config()
        path_to_key = config.stand.path_to_key
        operation = "destroy" if is_destroy else "create"
        stand_data = parse_manifest(
            path_to_stand_manifest,
            operation=operation,
            resource_roots=resource_roots,
        )
        stand = build_stand(stand_data, config, private_key=load_private_key(path_to_key))

        if is_destroy:
            stand.destroy()
            stand.output_destroy_result()
            return 0

        if not path_to_key.exists():
            with open(path_to_key, "w") as f:
                f.write(stand.key.private)

        stand.up(diagnostic=True)
        return 0
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(exc, file=sys.stderr)
        return 1


def cli() -> int:
    return main(sys.argv)


if __name__ == "__main__":
    sys.exit(cli())
