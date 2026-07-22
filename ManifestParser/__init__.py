import os
import re
from pathlib import Path
from typing import Any, Self, cast

import yaml

from ManifestParser.validation import validate_manifest


SECRET_TAG = "!secret"
SECRET_ENV_PREFIX = "SECRET_"
SECRET_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


class SecretReference(str):
    name: str
    env_name: str
    source: str

    def __new__(cls, name: str, source: str) -> Self:
        env_name = secret_env_name(name)
        value = cast(Self, str.__new__(cls, f"<unresolved-secret:{env_name}>"))
        value.name = name
        value.env_name = env_name
        value.source = source
        return value


class ManifestLoader(yaml.CSafeLoader):
    pass


def secret_env_name(name: str) -> str:
    if not SECRET_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"Secret name {name!r} must match [A-Za-z][A-Za-z0-9_-]*"
        )

    return SECRET_ENV_PREFIX + name.upper().replace("-", "_")


def _construct_secret(loader: ManifestLoader, node: yaml.Node) -> SecretReference:
    if not isinstance(node, yaml.ScalarNode):
        raise ValueError(
            f"{SECRET_TAG} at {node.start_mark.name}:{node.start_mark.line + 1} "
            "must be applied to a scalar value"
        )

    name = loader.construct_scalar(node)
    source = f"{node.start_mark.name}:{node.start_mark.line + 1}"
    return SecretReference(name, source)


ManifestLoader.add_constructor(SECRET_TAG, _construct_secret)


def parse_yml(path_to_yml: Path) -> dict:
    if not path_to_yml.exists():
        raise FileNotFoundError(f"Stand manifest {path_to_yml} does not exist")

    if not Path(path_to_yml).suffix.lower() in {".yml", ".yaml"}:
        raise TypeError(f"Stand manifest {path_to_yml} must be a YAML file")

    with open(path_to_yml, "rb") as f:
        try:
            data = yaml.load(f, Loader=ManifestLoader)
        except yaml.YAMLError as exc:
            raise TypeError(f"Error parsing stand manifest {path_to_yml} YAML file") from exc

    if not isinstance(data, dict):
        raise TypeError(f"Stand manifest {path_to_yml} must contain a YAML mapping")

    _reject_secret_mapping_keys(data)

    return data


def parse_manifest(path_to_manifest: Path, operation: str = "create") -> dict:
    if operation not in {"create", "destroy"}:
        raise ValueError("Manifest operation must be 'create' or 'destroy'")

    data = parse_yml(path_to_manifest)
    resolved_data = _resolve_dep_manifests(data, path_to_manifest)
    resolved_data = _resolve_secrets(resolved_data, operation)
    _normalize_local_resource_paths(resolved_data, path_to_manifest.parent)
    validate_manifest(resolved_data)
    return resolved_data


def _reject_secret_mapping_keys(value: Any, path: str = "manifest") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, SecretReference):
                raise ValueError(
                    f"{key.source}: {SECRET_TAG} cannot be used as a mapping key at {path}"
                )
            _reject_secret_mapping_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_mapping_keys(item, f"{path}[{index}]")


def _resolve_secrets(data: dict, operation: str) -> dict:
    missing: dict[str, list[str]] = {}

    def resolve(value: Any, path: str) -> Any:
        if isinstance(value, SecretReference):
            secret_value = os.environ.get(value.env_name)
            if secret_value:
                return secret_value

            if operation == "destroy" and _is_optional_destroy_secret(path):
                return value

            missing.setdefault(value.env_name, []).append(path)
            return value

        if isinstance(value, dict):
            return {key: resolve(item, f"{path}.{key}") for key, item in value.items()}

        if isinstance(value, list):
            return [resolve(item, f"{path}[{index}]") for index, item in enumerate(value)]

        return value

    resolved = {
        key: resolve(value, f"manifest.{key}")
        for key, value in data.items()
    }
    if missing:
        details = "; ".join(
            f"{env_name} ({', '.join(paths)})"
            for env_name, paths in sorted(missing.items())
        )
        raise ValueError(f"Missing or empty manifest secrets: {details}")

    return resolved


def _is_optional_destroy_secret(path: str) -> bool:
    if path.startswith("manifest.registries.") and path.rsplit(".", 1)[-1] in {
        "username",
        "password",
    }:
        return True

    return ".preferences." in path or path.endswith(".preferences")


def _resolve_dep_manifests(data: dict, manifest_path: Path) -> dict:
    resolved = {
        key: _resolve_value(value, manifest_path)
        for key, value in data.items()
        if key != "from_dep_manifest"
    }

    if "from_dep_manifest" not in data:
        _normalize_local_resource_paths(resolved, manifest_path.parent)
        return resolved

    dep_manifest = data["from_dep_manifest"]
    if isinstance(dep_manifest, SecretReference):
        secret_value = os.environ.get(dep_manifest.env_name)
        if not secret_value:
            raise ValueError(
                "Missing or empty structural manifest secret: "
                f"{dep_manifest.env_name} ({manifest_path}.from_dep_manifest)"
            )
        dep_manifest = secret_value

    if not isinstance(dep_manifest, str):
        raise ValueError(f"{manifest_path}.from_dep_manifest must be a string")

    dep_manifest_path = _resolve_path(dep_manifest, manifest_path.parent)
    dep_data = parse_yml(dep_manifest_path)
    resolved_dep_data = _resolve_dep_manifests(dep_data, dep_manifest_path)

    conflicting_keys = {
        key
        for key in set(resolved_dep_data).intersection(resolved)
        if resolved_dep_data[key] != resolved[key]
    }
    if conflicting_keys:
        raise ValueError(
            f"{manifest_path}.from_dep_manifest conflicts with local keys: {sorted(conflicting_keys)}"
        )

    merged = {**resolved_dep_data, **resolved}
    _normalize_dep_resource_paths(merged, dep_manifest_path.parent)
    return merged


def _resolve_value(value, current_manifest_path: Path):
    if isinstance(value, dict):
        resolved = _resolve_dep_manifests(value, current_manifest_path)
        _normalize_local_resource_paths(resolved, current_manifest_path.parent)
        return resolved

    if isinstance(value, list):
        return [_resolve_value(item, current_manifest_path) for item in value]

    return value


def _resolve_path(path: str, base_dir: Path) -> Path:
    current = Path(path)
    if current.is_absolute():
        return current

    return base_dir / current


def _normalize_dep_resource_paths(data: dict, dep_manifest_dir: Path) -> None:
    _normalize_template_and_hook_paths(data, dep_manifest_dir)


def _normalize_template_and_hook_paths(data: dict, base_dir: Path) -> None:
    templates = data.get("templates")
    if isinstance(templates, dict):
        for template in templates.values():
            if isinstance(template, dict) and isinstance(template.get("path"), str):
                template["path"] = str(_resolve_path(template["path"], base_dir).resolve())

    instances = data.get("instances")
    if isinstance(instances, dict):
        for instance in instances.values():
            if isinstance(instance, dict) and isinstance(instance.get("hooks"), str):
                instance["hooks"] = str(_resolve_path(instance["hooks"], base_dir).resolve())


def _normalize_local_resource_paths(data: dict, base_dir: Path) -> None:
    node_profiles = data.get("node_profiles")
    if isinstance(node_profiles, dict):
        for profile in node_profiles.values():
            if isinstance(profile, dict) and isinstance(profile.get("cloud-init"), str):
                profile["cloud-init"] = str(_resolve_path(profile["cloud-init"], base_dir).resolve())

    _normalize_template_and_hook_paths(data, base_dir)
