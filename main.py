import sys
from pathlib import Path

from config.config import Config
from ManifestParser import parse_manifest
from StandBuilder import build_stand


def parse_args(argv: list[str]) -> tuple[bool, Path]:
    if len(argv) != 3 or argv[1] not in {"create", "destroy"}:
        print("Usage: python main.py <create|destroy> <path_to_stand_manifest>")
        sys.exit(1)

    return argv[1] == "destroy", Path(argv[2])


def load_private_key(path_to_key: Path) -> str:
    if not path_to_key.exists():
        return ""

    with open(path_to_key, "r") as f:
        return f.read()


def main(argv: list[str]) -> int:
    is_destroy, path_to_stand_manifest = parse_args(argv)
    config = Config()
    path_to_key = config.stand.path_to_key

    try:
        stand_data = parse_manifest(path_to_stand_manifest)
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

    stand.up(diagnostic=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
