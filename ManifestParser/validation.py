def validate_manifest(manifest: dict) -> None:
    _require_mapping(manifest, "manifest")

    stand = _require_mapping_key(manifest, "stand", "manifest")
    registries = _require_mapping_key(manifest, "registries", "manifest")
    apps = _require_mapping_key(manifest, "apps", "manifest")
    node_profiles = _require_mapping_key(manifest, "node_profiles", "manifest")
    nodes = _require_mapping_key(manifest, "nodes", "manifest")

    _validate_stand(stand)
    _validate_registries(registries)
    instance_to_app = _validate_apps(apps, registries)
    _validate_node_profiles(node_profiles)
    _validate_nodes(nodes, node_profiles, instance_to_app)


def _validate_stand(stand: dict) -> None:
    users = _require_mapping_key(stand, "users", "manifest.stand")
    ssh = _require_mapping_key(stand, "ssh", "manifest.stand")

    _require_string_key(stand, "project", "manifest.stand")
    _require_string_key(stand, "env", "manifest.stand")
    _require_string_key(users, "sudo", "manifest.stand.users")
    _require_string_key(users, "app", "manifest.stand.users")
    _require_string_key(ssh, "key_name_admin", "manifest.stand.ssh")


def _validate_registries(registries: dict) -> None:
    if not registries:
        raise ValueError("manifest.registries must not be empty")

    for registry_name, registry in registries.items():
        registry_path = f"manifest.registries.{registry_name}"
        _require_non_empty_name(registry_name, "manifest.registries")
        _require_mapping(registry, registry_path)
        _require_string_key(registry, "url", registry_path)

        has_username = bool(registry.get("username"))
        has_password = bool(registry.get("password"))
        if has_username != has_password:
            raise ValueError(f"{registry_path} username and password must be set together")

        if "insecure" in registry and not isinstance(registry["insecure"], bool):
            raise ValueError(f"{registry_path}.insecure must be a boolean")


def _validate_apps(apps: dict, registries: dict) -> dict[str, str]:
    if not apps:
        raise ValueError("manifest.apps must not be empty")

    instance_to_app = {}

    for app_name, app in apps.items():
        app_path = f"manifest.apps.{app_name}"
        _require_non_empty_name(app_name, "manifest.apps")
        _require_mapping(app, app_path)

        declared_name = _require_string_key(app, "name", app_path)
        if declared_name != app_name:
            raise ValueError(f"{app_path}.name must match app key {app_name!r}")

        image = _require_mapping_key(app, "image", app_path)
        roles = _require_mapping_key(app, "roles", app_path)
        templates = _require_mapping_key(app, "templates", app_path)
        instances = _require_mapping_key(app, "instances", app_path)

        if not roles:
            raise ValueError(f"{app_path}.roles must not be empty")
        if not templates:
            raise ValueError(f"{app_path}.templates must not be empty")
        if not instances:
            raise ValueError(f"{app_path}.instances must not be empty")

        _validate_image(image, registries, f"{app_path}.image")
        _validate_roles(roles, f"{app_path}.roles")
        _validate_templates(templates, f"{app_path}.templates")
        _validate_instances(instances, roles, app_name, instance_to_app, f"{app_path}.instances")

        if "preferences" in app:
            _require_mapping(app["preferences"], f"{app_path}.preferences")

    return instance_to_app


def _validate_image(image: dict, registries: dict, path: str) -> None:
    registry_name = _require_string_key(image, "registry", path)
    if registry_name not in registries:
        raise ValueError(f"{path}.registry references unknown registry {registry_name!r}")

    _require_string_key(image, "path", path)
    _require_string_key(image, "version", path)


def _validate_roles(roles: dict, path: str) -> None:
    for role_name, role in roles.items():
        role_path = f"{path}.{role_name}"
        _require_non_empty_name(role_name, path)
        _require_mapping(role, role_path)

        if "ports" not in role:
            continue

        ports = role["ports"]
        if not isinstance(ports, list):
            raise ValueError(f"{role_path}.ports must be a list")

        for index, port in enumerate(ports):
            port_path = f"{role_path}.ports[{index}]"
            _require_mapping(port, port_path)
            _require_int_key(port, "number", port_path)
            protocol = _require_string_key(port, "protocol", port_path)
            if protocol not in {"tcp", "udp"}:
                raise ValueError(f"{port_path}.protocol must be 'tcp' or 'udp'")

            _require_string_key(port, "zone", port_path)

        if "preferences" in role:
            _require_mapping(role["preferences"], f"{role_path}.preferences")


def _validate_templates(templates: dict, path: str) -> None:
    for template_name, template in templates.items():
        template_path = f"{path}.{template_name}"
        _require_non_empty_name(template_name, path)
        _require_mapping(template, template_path)

        for key in ("path", "dest", "owner", "mode"):
            _require_string_key(template, key, template_path)


def _validate_instances(
    instances: dict,
    roles: dict,
    app_name: str,
    instance_to_app: dict[str, str],
    path: str,
) -> None:
    for instance_name, instance in instances.items():
        instance_path = f"{path}.{instance_name}"
        _require_non_empty_name(instance_name, path)
        _require_mapping(instance, instance_path)

        role_name = _require_string_key(instance, "role", instance_path)
        if role_name not in roles:
            raise ValueError(f"{instance_path}.role references unknown role {role_name!r}")

        if instance_name in instance_to_app:
            raise ValueError(
                f"{instance_path} duplicates instance declared in app {instance_to_app[instance_name]!r}"
            )
        instance_to_app[instance_name] = app_name

        if "preferences" in instance:
            _require_mapping(instance["preferences"], f"{instance_path}.preferences")
        if "hooks" in instance:
            _require_string_key(instance, "hooks", instance_path)


def _validate_node_profiles(node_profiles: dict) -> None:
    if not node_profiles:
        raise ValueError("manifest.node_profiles must not be empty")

    for profile_name, profile in node_profiles.items():
        profile_path = f"manifest.node_profiles.{profile_name}"
        _require_non_empty_name(profile_name, "manifest.node_profiles")
        _require_mapping(profile, profile_path)

        for key in ("location", "type_serv", "image", "network", "cloud-init"):
            _require_string_key(profile, key, profile_path)

        if "app_runtime" in profile:
            _require_string_key(profile, "app_runtime", profile_path)


def _validate_nodes(nodes: dict, node_profiles: dict, instance_to_app: dict[str, str]) -> None:
    if not nodes:
        raise ValueError("manifest.nodes must not be empty")

    assigned_instances = {}

    for node_name, node in nodes.items():
        node_path = f"manifest.nodes.{node_name}"
        _require_non_empty_name(node_name, "manifest.nodes")
        _require_mapping(node, node_path)

        profile_name = node.get("profile", "default")
        if not isinstance(profile_name, str) or not profile_name:
            raise ValueError(f"{node_path}.profile must be a non-empty string")
        if profile_name not in node_profiles:
            raise ValueError(f"{node_path}.profile references unknown profile {profile_name!r}")

        apps = _require_list_key(node, "apps", node_path)
        for index, instance_name in enumerate(apps):
            app_path = f"{node_path}.apps[{index}]"
            if not isinstance(instance_name, str) or not instance_name:
                raise ValueError(f"{app_path} must be a non-empty string")
            if instance_name not in instance_to_app:
                raise ValueError(f"{app_path} references unknown instance {instance_name!r}")
            if instance_name in assigned_instances:
                raise ValueError(
                    f"{app_path} instance {instance_name!r} is already assigned to node "
                    f"{assigned_instances[instance_name]!r}"
                )

            assigned_instances[instance_name] = node_name

    for instance_name, app_name in instance_to_app.items():
        if instance_name not in assigned_instances:
            raise ValueError(f"manifest.apps.{app_name}.instances.{instance_name} is not assigned to any node")


def _require_mapping(value, path: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a mapping")


def _require_mapping_key(data: dict, key: str, path: str) -> dict:
    if key not in data:
        raise ValueError(f"{path}.{key} is required")

    value = data[key]
    if not isinstance(value, dict):
        raise ValueError(f"{path}.{key} must be a mapping")

    return value


def _require_string_key(data: dict, key: str, path: str) -> str:
    if key not in data:
        raise ValueError(f"{path}.{key} is required")

    value = data[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path}.{key} must be a non-empty string")

    return value


def _require_int_key(data: dict, key: str, path: str) -> int:
    if key not in data:
        raise ValueError(f"{path}.{key} is required")

    value = data[key]
    if not isinstance(value, int):
        raise ValueError(f"{path}.{key} must be an integer")

    return value


def _require_list_key(data: dict, key: str, path: str) -> list:
    if key not in data:
        raise ValueError(f"{path}.{key} is required")

    value = data[key]
    if not isinstance(value, list):
        raise ValueError(f"{path}.{key} must be a list")

    return value


def _require_non_empty_name(value, path: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path} keys must be non-empty strings")
