from pathlib import Path

from App import App, ClusterApp, ConfigFile, RoleApp
from ShellCollect import Image, ImageRegistry, Port
from StandFramework import Node, Stand, StandState
from config.config import Config


DEFAULT_APP_RUNTIME = "podman"


def build_stand(stand_data: dict, config: Config, private_key: str = "") -> Stand:
    stand_data = _expand_agent_apps(stand_data)
    registries = _build_registries(stand_data["registries"])
    clusters = []
    instances_by_name = {}

    for app_name, app_data in stand_data["apps"].items():
        cluster, app_instances = _build_cluster(app_name, app_data, registries)
        clusters.append(cluster)
        instances_by_name.update(app_instances)

    nodes = _build_nodes(stand_data["nodes"], stand_data["node_profiles"], instances_by_name)
    runtimes = {node.app_runtime for node in nodes.values()}
    if len(runtimes) != 1:
        raise ValueError(f"Only one app_runtime per stand is currently supported, got {sorted(runtimes)}")

    state = _build_stand_state(stand_data["stand"], config)
    users = stand_data["stand"]["users"]
    ssh = stand_data["stand"]["ssh"]

    return Stand(
        private_key=private_key,
        sudo_user=users["sudo"],
        app_user=users["app"],
        key_name_admin=ssh["key_name_admin"],
        nodes=nodes,
        state=state,
        clusters=clusters,
        path_folder_configset=config.stand.path_to_configset / f"{config.stand.user}_{state.project}_{state.env}",
        APP_RUNTIME=next(iter(runtimes)),
        output_console=config.output.console,
        output_console_secrets=config.output.console_secrets,
        output_file=config.output.file,
        output_file_directory=config.output.file_path,
    )


def _expand_agent_apps(stand_data: dict) -> dict:
    expanded = _copy_manifest_value(stand_data)
    agent_instances = expanded.pop("agents", {}).get("apps", [])
    if not agent_instances:
        return expanded

    instance_locations = {
        instance_name: (app_name, instance_data)
        for app_name, app_data in expanded["apps"].items()
        for instance_name, instance_data in app_data["instances"].items()
    }

    for instance_name in agent_instances:
        app_name, instance_data = instance_locations[instance_name]
        app_instances = expanded["apps"][app_name]["instances"]
        del app_instances[instance_name]

        for node_name, node_data in expanded["nodes"].items():
            generated_name = f"{instance_name}--{node_name}"
            app_instances[generated_name] = _copy_manifest_value(instance_data)
            node_data["apps"].append(generated_name)

    return expanded


def _copy_manifest_value(value):
    if isinstance(value, dict):
        return {key: _copy_manifest_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_manifest_value(item) for item in value]
    return value


def _build_registries(registries_data: dict) -> dict[str, ImageRegistry]:
    return {
        name: ImageRegistry(
            url=data["url"],
            username=data.get("username"),
            password=data.get("password"),
            insecure=data.get("insecure", False),
        )
        for name, data in registries_data.items()
    }


def _build_cluster(
    app_name: str,
    app_data: dict,
    registries: dict[str, ImageRegistry],
) -> tuple[ClusterApp, dict[str, App]]:
    roles = _build_roles(app_data["roles"])
    image_data = app_data["image"]
    image = Image(
        registry=registries[image_data["registry"]],
        path=image_data["path"],
        version=str(image_data["version"]),
    )

    instances = []
    instances_by_name = {}
    for instance_name, instance_data in app_data["instances"].items():
        instance = App(
            name=instance_name,
            role=roles[instance_data["role"]],
            cpu=instance_data["cpu"],
            ram=instance_data["ram"],
            oom_priority=instance_data.get("oom_priority"),
            hook_path=Path(instance_data["hooks"]) if "hooks" in instance_data else None,
            preferences=instance_data.get("preferences", {}),
        )
        instances.append(instance)
        instances_by_name[instance_name] = instance

    templates = {
        name: _build_config_file(template_data)
        for name, template_data in app_data["templates"].items()
    }

    cluster = ClusterApp(
        name=app_name,
        image=image,
        preferences=app_data.get("preferences", {}),
        instances_app=instances,
        paths_to_templates=templates,
        connection_template=Path(app_data["connection"]) if "connection" in app_data else None,
        connection_instance_name=app_data.get("connection_instance"),
    )

    return cluster, instances_by_name


def _build_roles(roles_data: dict) -> dict[str, RoleApp]:
    return {
        name: RoleApp(
            name=name,
            ports=[_build_port(port_data) for port_data in role_data.get("ports", [])],
            preferences=role_data.get("preferences", {}),
        )
        for name, role_data in roles_data.items()
    }


def _build_port(port_data: dict) -> Port:
    return Port(
        number=port_data["number"],
        protocol=port_data["protocol"],
        zone=port_data["zone"],
    )


def _build_config_file(template_data: dict) -> ConfigFile:
    return ConfigFile(
        paths_to_templates=Path(template_data["path"]),
        dest=template_data["dest"],
        owner=template_data["owner"],
        mode=template_data["mode"],
    )


def _build_nodes(
    nodes_data: dict,
    node_profiles: dict,
    instances_by_name: dict[str, App],
) -> dict[str, Node]:
    nodes = {}

    for node_name, node_data in nodes_data.items():
        node_config = _load_node_config(node_data, node_profiles)
        instances_app = [
            instances_by_name[instance_name]
            for instance_name in node_data["apps"]
        ]
        nodes[node_name] = Node(**node_config, instances_app=instances_app)

    return nodes


def _load_node_config(node_data: dict, node_profiles: dict) -> dict:
    profile = dict(node_profiles[node_data.get("profile", "default")])

    for key in ("location", "type_serv", "image", "network", "cloud-init", "app_runtime"):
        if key in node_data:
            profile[key] = node_data[key]

    profile.setdefault("app_runtime", DEFAULT_APP_RUNTIME)
    profile["cloud_init_template"] = Path(profile.pop("cloud-init"))
    return profile


def _build_stand_state(stand_data: dict, config: Config) -> StandState:
    return StandState(
        owner=config.stand.user,
        passphrase=config.stand.passphrase,
        project=stand_data["project"],
        env=stand_data["env"],
    )
