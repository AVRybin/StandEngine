from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from mako.template import Template
import yaml

from InfraBaseLib.SShExecutor.uploder import UploadFilesCollector
from InfraBaseLib.helpers.cloud_init import CloudInit

if TYPE_CHECKING:
    from StandFramework.stand.stand import InstanceApp, Stand


MODE_PATTERN = re.compile(r"^[0-7]{3,4}$")
OWNER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*[$]?$")
PLACEHOLDER_SSH_PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKf7nE4A2Xqv1S8U0X7P9mHq0Wz6bV3tR5cY2aL8dF1 "
    "stands-engine-preflight"
)


@dataclass(frozen=True)
class PreflightIssue:
    subject: str
    message: str


class PreflightValidationError(ValueError):
    def __init__(self, issues: list[PreflightIssue]):
        self.issues = issues
        lines = [f"Local preflight found {len(issues)} error(s):"]
        lines.extend(
            f"  {index}. {issue.subject}: {issue.message}"
            for index, issue in enumerate(issues, start=1)
        )
        super().__init__("\n".join(lines))


class StandPreflightValidator:
    """Validate all locally knowable deployment inputs without writing files."""

    def __init__(self, stand: Stand):
        self.stand = stand
        self.issues: list[PreflightIssue] = []
        self._secret_values = self._collect_sensitive_values()
        self._host_ports: dict[tuple[int, int, str], str] = {}
        self._remote_destinations: dict[tuple[int, str], str] = {}

    def validate(self) -> None:
        original_addresses = self._install_placeholder_addresses()
        try:
            self._validate_result_contract()
            self._validate_cloud_init_templates()
            self._validate_app_templates()
            self._validate_hooks()
            self._validate_connections()
        finally:
            self._restore_addresses(original_addresses)

        if self.issues:
            raise PreflightValidationError(self.issues)

    def _add(self, subject: str, message: str | Exception) -> None:
        rendered = str(message)
        for secret in self._secret_values:
            rendered = rendered.replace(secret, "***")
        self.issues.append(PreflightIssue(subject, rendered))

    def _collect_sensitive_values(self) -> list[str]:
        values: set[str] = set()

        def collect(value: Any) -> None:
            if isinstance(value, dict):
                for item in value.values():
                    collect(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    collect(item)
            elif isinstance(value, str) and value:
                values.add(value)

        for cluster in self.stand.clusters_app.values():
            collect(cluster.preferences)
            registry = cluster.image.registry
            collect({"username": registry.username, "password": registry.password})
            for app in cluster.instances_app:
                collect(app.preferences)
                collect(app.role.preferences)

        return sorted(values, key=len, reverse=True)

    def _install_placeholder_addresses(self) -> dict[int, tuple[bool, Any, bool, Any]]:
        originals = {}
        for index, node in enumerate(self.stand.nodes.values(), start=1):
            had_private = hasattr(node, "private_ip")
            had_public = hasattr(node, "public_ip")
            originals[id(node)] = (
                had_private,
                getattr(node, "private_ip", None),
                had_public,
                getattr(node, "public_ip", None),
            )
            third_octet, fourth_octet = divmod(index - 1, 254)
            node.private_ip = f"10.255.{third_octet % 256}.{fourth_octet + 1}"
            node.public_ip = f"192.0.2.{fourth_octet + 1}"
        return originals

    def _restore_addresses(self, originals: dict[int, tuple[bool, Any, bool, Any]]) -> None:
        for node in self.stand.nodes.values():
            had_private, private, had_public, public = originals[id(node)]
            if had_private:
                node.private_ip = private
            else:
                del node.private_ip
            if had_public:
                node.public_ip = public
            else:
                del node.public_ip

    def _validate_result_contract(self) -> None:
        try:
            self.stand.validate_result_contract()
        except Exception as exc:
            self._add("result output", exc)

    def _validate_cloud_init_templates(self) -> None:
        seen: set[Path] = set()
        for node_name, node in self.stand.nodes.items():
            path = Path(node.cloud_init_template)
            if path in seen:
                continue
            seen.add(path)
            subject = f"cloud-init for node {node_name!r} ({path})"
            if not path.is_file():
                self._add(subject, "template file does not exist")
                continue
            try:
                rendered = CloudInit.render(
                    user_admin=self.stand.sudo_user,
                    ssh_public_key=PLACEHOLDER_SSH_PUBLIC_KEY,
                    user_app=self.stand.app_user,
                    template_path=path,
                    network_ip_range="10.255.0.0/16",
                )
            except Exception as exc:
                self._add(subject, f"Mako render failed: {type(exc).__name__}: {exc}")
                continue
            try:
                document = yaml.safe_load(rendered)
                if not isinstance(document, dict) or not document:
                    raise ValueError("rendered cloud-init must be a non-empty YAML mapping")
            except yaml.YAMLError as exc:
                self._add(subject, self._yaml_error(exc))
            except ValueError as exc:
                self._add(subject, exc)

    def _validate_app_templates(self) -> None:
        for cluster_name, cluster in self.stand.clusters_app.items():
            if "pod" not in cluster.paths_to_templates:
                self._add(f"app {cluster_name!r}", "templates must contain required key 'pod'")

            destinations: dict[str, str] = {}
            local_names: dict[str, str] = {}
            for template_name, config in cluster.paths_to_templates.items():
                subject = f"app {cluster_name!r} template {template_name!r}"
                self._validate_upload_metadata(subject, config.dest, config.owner, config.mode)

                previous = destinations.setdefault(config.dest, template_name)
                if previous != template_name:
                    self._add(subject, f"destination {config.dest!r} is also used by template {previous!r}")
                local_name = config.paths_to_templates.name.removesuffix(".mako")
                previous = local_names.setdefault(local_name, template_name)
                if previous != template_name:
                    self._add(subject, f"configset filename {local_name!r} is also produced by template {previous!r}")

                path = Path(config.paths_to_templates)
                if not path.is_file():
                    self._add(f"{subject} ({path})", "template file does not exist")
                    continue

                for app in cluster.instances_app:
                    instance = self.stand.instance_apps[app.name]
                    render_subject = f"{subject}, instance {app.name!r} ({path})"
                    destination_key = (id(instance.node), config.dest)
                    destination_owner = f"{cluster_name}/{app.name}/{template_name}"
                    previous_owner = self._remote_destinations.setdefault(
                        destination_key, destination_owner
                    )
                    if previous_owner != destination_owner:
                        self._add(
                            render_subject,
                            f"destination {config.dest!r} conflicts on the same node with {previous_owner}",
                        )
                    try:
                        rendered = self.stand.render_app_template(path, instance)
                    except Exception as exc:
                        self._add(render_subject, f"Mako render failed: {type(exc).__name__}: {exc}")
                        continue
                    if template_name == "pod":
                        self._validate_pod_yaml(render_subject, rendered, instance)

    def _validate_upload_metadata(self, subject: str, dest: str, owner: str, mode: str) -> None:
        if not MODE_PATTERN.fullmatch(mode):
            self._add(subject, f"mode {mode!r} must be a 3- or 4-digit octal value")
        if not OWNER_PATTERN.fullmatch(owner):
            self._add(subject, f"owner {owner!r} is not a valid local account name")
        try:
            UploadFilesCollector.home_relative_path(dest, f"/home/{self.stand.app_user}")
        except ValueError as exc:
            self._add(subject, exc)

    def _validate_pod_yaml(self, subject: str, rendered: str, instance: InstanceApp) -> None:
        try:
            documents = [document for document in yaml.safe_load_all(rendered) if document is not None]
        except yaml.YAMLError as exc:
            self._add(subject, self._yaml_error(exc))
            return
        if not documents:
            self._add(subject, "rendered pod template contains no YAML documents")
            return

        pods = []
        for index, document in enumerate(documents, start=1):
            if not isinstance(document, dict):
                self._add(subject, f"YAML document {index} must be a mapping")
                continue
            for field in ("apiVersion", "kind"):
                if not isinstance(document.get(field), str) or not document[field]:
                    self._add(subject, f"YAML document {index}.{field} must be a non-empty string")
            metadata = document.get("metadata")
            if not isinstance(metadata, dict) or not isinstance(metadata.get("name"), str) or not metadata["name"]:
                self._add(subject, f"YAML document {index}.metadata.name must be a non-empty string")
            if document.get("kind") == "Pod":
                pods.append(document)

        if not pods:
            self._add(subject, "rendered pod template must contain at least one Pod document")
            return
        for pod in pods:
            self._record_host_ports(subject, pod, instance)

    def _record_host_ports(self, subject: str, pod: dict, instance: InstanceApp) -> None:
        spec = pod.get("spec")
        if not isinstance(spec, dict):
            self._add(subject, "Pod.spec must be a mapping")
            return
        containers = spec.get("containers")
        if not isinstance(containers, list) or not containers:
            self._add(subject, "Pod.spec.containers must be a non-empty list")
            return
        for container in containers:
            if not isinstance(container, dict):
                continue
            for port in container.get("ports", []) or []:
                if not isinstance(port, dict) or "hostPort" not in port:
                    continue
                host_port = port["hostPort"]
                if type(host_port) is not int or not 1 <= host_port <= 65535:
                    self._add(subject, f"hostPort must be an integer between 1 and 65535, got {host_port!r}")
                    continue
                protocol = str(port.get("protocol", "TCP")).upper()
                key = (id(instance.node), host_port, protocol)
                current = f"{instance.cluster.name}/{instance.app.name}"
                previous = self._host_ports.get(key)
                if previous is None:
                    self._host_ports[key] = current
                else:
                    self._add(subject, f"hostPort {host_port}/{protocol} conflicts on the same node with {previous}")

    def _validate_hooks(self) -> None:
        for instance in self.stand.instance_apps.values():
            if instance.app.hook_path is None:
                continue
            subject = f"hook for {instance.cluster.name}/{instance.app.name}"
            try:
                files = self.stand._collect_hook_files(instance)
            except Exception as exc:
                self._add(subject, exc)
                continue
            for path, relative_path, is_mako in files:
                if not is_mako:
                    continue
                try:
                    self.stand.render_app_template(path, instance)
                except Exception as exc:
                    self._add(
                        f"{subject} file {relative_path.as_posix()!r} ({path})",
                        f"Mako render failed: {type(exc).__name__}: {exc}",
                    )

    def _validate_connections(self) -> None:
        for cluster_name, cluster in self.stand.clusters_app.items():
            path = cluster.connection_template
            if path is None:
                continue
            path = Path(path)
            subject = f"connection for app {cluster_name!r} ({path})"
            if not path.is_file():
                self._add(subject, "template file does not exist")
                continue
            try:
                self.stand._connection_templates[cluster_name] = Template(filename=str(path))
                self.stand.render_connection(cluster)
            except Exception as exc:
                self._add(subject, f"validation failed: {type(exc).__name__}: {exc}")

    @staticmethod
    def _yaml_error(exc: yaml.YAMLError) -> str:
        mark = getattr(exc, "problem_mark", None)
        problem = getattr(exc, "problem", None) or type(exc).__name__
        if mark is None:
            return f"invalid YAML: {problem}"
        return f"invalid YAML at line {mark.line + 1}, column {mark.column + 1}: {problem}"
