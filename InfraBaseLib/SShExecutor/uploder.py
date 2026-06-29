from dataclasses import dataclass, field
from io import BytesIO
import posixpath
from shlex import quote
import tarfile
from typing import Any, Callable, Protocol

from pyinfra.api import Inventory
from pyinfra.operations import files


class InfraOperation(Protocol):
    operation: Callable[..., Any]

    def build_kwargs(self, inventory: Inventory) -> dict[str, Any]: ...


@dataclass(kw_only=True)
class UploadAsset:
    content: str | bytes
    dest: str
    owner: str
    mode: str


@dataclass(kw_only=True)
class UploadBinaryFile:
    name: str
    content: bytes
    dest: str
    for_group: str
    mode: str
    operation: Callable[..., Any] = field(init=False)

    def __post_init__(self):
        self.operation = files.put

    def build_kwargs(self, inventory: Inventory) -> dict[str, Any]:
        return {
            "name": self.name,
            "src": BytesIO(self.content),
            "dest": self.dest,
            "mode": self.mode,
            "host": inventory.get_group(self.for_group),
            "_sudo": True,
        }


@dataclass(kw_only=True)
class UploadFilesCollector:
    upload_files: dict[str, list[UploadAsset]] = field(default_factory=dict)

    def add_upload_asset(self, node_group: str, asset: UploadAsset) -> None:
        self.upload_files.setdefault(node_group, []).append(asset)

    def clear_upload_files(self) -> None:
        self.upload_files = {}

    def build_node_upload_archive(self, app_user: str, assets: list[UploadAsset]) -> bytes:
        home_dir = f"/home/{app_user}"
        tar_buffer = BytesIO()

        with tarfile.open(fileobj=tar_buffer, mode="w:zst") as archive:
            for asset in assets:
                archive_path = self.home_relative_path(asset.dest, home_dir)
                content = asset.content.encode("utf-8") if isinstance(asset.content, str) else asset.content

                tar_info = tarfile.TarInfo(archive_path)
                tar_info.size = len(content)
                tar_info.mode = int(asset.mode, 8)
                tar_info.uname = asset.owner
                tar_info.gname = asset.owner

                archive.addfile(tar_info, BytesIO(content))

        return tar_buffer.getvalue()

    @staticmethod
    def home_relative_path(dest: str, home_dir: str) -> str:
        normalized_dest = posixpath.normpath(dest)
        normalized_home = posixpath.normpath(home_dir)

        if normalized_dest == normalized_home:
            raise ValueError(f"Upload destination must be a file path under {home_dir}: {dest}")

        home_prefix = normalized_home + "/"
        if not normalized_dest.startswith(home_prefix):
            raise ValueError(f"Upload destination must be under {home_dir}: {dest}")

        relative_path = normalized_dest.removeprefix(home_prefix)
        if relative_path.startswith("../") or relative_path == "..":
            raise ValueError(f"Upload destination escapes {home_dir}: {dest}")

        return relative_path

    def build_upload_archive_operations(self, app_user: str) -> list[InfraOperation]:
        operations: list[InfraOperation] = []

        for node_group, assets in self.upload_files.items():
            if not assets:
                continue

            archive_path = f"/tmp/stands-engine-upload-{self.safe_archive_name(node_group)}.tar.zst"
            operations.append(UploadBinaryFile(
                name=f"Upload files archive {node_group}",
                content=self.build_node_upload_archive(app_user, assets),
                dest=archive_path,
                for_group=node_group,
                mode="600",
            ))
            operations.append(UnpackArchiveCommand(
                name=f"Unpack files archive {node_group}",
                cmd=self.build_unpack_command(app_user, archive_path, assets),
                for_group=node_group,
            ))

        return operations

    @staticmethod
    def safe_archive_name(value: str) -> str:
        return "".join(char if char.isalnum() or char in ".-" else "-" for char in value)

    def build_unpack_command(self, app_user: str, archive_path: str, assets: list[UploadAsset]) -> str:
        home_dir = f"/home/{app_user}"
        commands = [
            f"mkdir -p {quote(home_dir)}",
            f"tar --zstd -xf {quote(archive_path)} -C {quote(home_dir)}",
        ]

        for directory, owner in self.upload_asset_directories(app_user, assets).items():
            quoted_owner = quote(owner)
            quoted_directory = quote(directory)
            commands.append(f"chown {quoted_owner}:$(id -gn {quoted_owner}) {quoted_directory}")
            commands.append(f"chmod 755 {quoted_directory}")

        for asset in assets:
            owner = quote(asset.owner)
            dest = quote(posixpath.normpath(asset.dest))
            commands.append(f"chown {owner}:$(id -gn {owner}) {dest}")
            commands.append(f"chmod {quote(asset.mode)} {dest}")

        commands.append(f"rm -f {quote(archive_path)}")
        return " && ".join(commands)

    def upload_asset_directories(self, app_user: str, assets: list[UploadAsset]) -> dict[str, str]:
        home_dir = f"/home/{app_user}"
        directories: dict[str, str] = {}

        for asset in assets:
            relative_path = self.home_relative_path(asset.dest, home_dir)
            parent = posixpath.dirname(relative_path)
            parts = [] if parent in ["", "."] else parent.split("/")

            current = home_dir
            for part in parts:
                current = posixpath.join(current, part)
                directories.setdefault(current, asset.owner)

        return directories


@dataclass(kw_only=True)
class UnpackArchiveCommand:
    name: str
    cmd: str
    for_group: str
    operation: Callable[..., Any] = field(init=False)

    def __post_init__(self):
        from pyinfra.operations import server as op_server

        self.operation = op_server.shell

    def build_kwargs(self, inventory: Inventory) -> dict[str, Any]:
        return {
            "name": self.name,
            "commands": [
                self.cmd,
            ],
            "_sudo": True,
            "host": inventory.get_group(self.for_group),
        }
