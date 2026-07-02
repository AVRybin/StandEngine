from pathlib import Path
import yaml

from ManifestParser.validation import validate_manifest


def parse_yml(path_to_yml: Path) -> dict:
    if not path_to_yml.exists():
        raise FileNotFoundError(f"Stand manifest {path_to_yml} does not exist")

    if not Path(path_to_yml).suffix.lower() in {".yml", ".yaml"}:
        raise TypeError(f"Stand manifest {path_to_yml} must be a YAML file")

    with open(path_to_yml, "rb") as f:
        try:
            data = yaml.load(f, Loader=yaml.CSafeLoader)
        except yaml.YAMLError as exc:
            raise TypeError(f"Error parsing stand manifest {path_to_yml} YAML file") from exc

    if not isinstance(data, dict):
        raise TypeError(f"Stand manifest {path_to_yml} must contain a YAML mapping")

    return data


def parse_manifest(path_to_manifest: Path) -> dict:
    data = parse_yml(path_to_manifest)
    resolved_data = _resolve_dep_manifests(data, path_to_manifest)
    _normalize_local_resource_paths(resolved_data, path_to_manifest.parent)
    validate_manifest(resolved_data)
    return resolved_data


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
