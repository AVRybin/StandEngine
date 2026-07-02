from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Callable

import pulumi
import pulumi_hcloud as hcloud

from InfraBaseLib.helpers.cloud_init import CloudInit

@dataclass(kw_only=True)
class Server:
    location: str
    type: str
    network: str
    image: str
    cloud_init_template: Path | None = None
    sudo_user: str = ""
    ssh_public_key: str = ""
    app_user: str = ""
    labels: dict[str, str] = field(default_factory=dict)

@dataclass(kw_only=True)
class ServersDesigner:
    ssh_admin_name: str

    @staticmethod
    def safe_output_name(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_") or "unknown"

    def get_program(self, servers: dict[str, Server]) -> Callable[[], None]:
        def program() -> None:
            map_network = {}

            for name, server in servers.items():
                if server.network not in map_network:
                    target_network = hcloud.get_network(name=server.network)
                    map_network[server.network] = target_network
                    pulumi.export(
                        f"network_{self.safe_output_name(server.network)}_ip_range",
                        target_network.ip_range,
                    )

                target_network = map_network[server.network]
                network_args = {
                    "network_id": target_network.id,
                }
                if server.cloud_init_template is None:
                    raise ValueError(f"Server {name} has no cloud-init template")

                user_data = CloudInit.render(
                    user_admin=server.sudo_user,
                    ssh_public_key=server.ssh_public_key,
                    user_app=server.app_user,
                    template_path=server.cloud_init_template,
                    network_ip_range=target_network.ip_range,
                )

                curr_server = hcloud.Server(
                    name,
                    name=name,
                    image=server.image,
                    server_type=server.type,
                    location=server.location,
                    ssh_keys=[self.ssh_admin_name],
                    networks=[network_args],
                    user_data=user_data,
                    labels=server.labels,
                    opts=pulumi.ResourceOptions(
                        ignore_changes=["user_data"],
                    ),
                )

                pulumi.export(f"server_{name}_internal_ip", curr_server.networks[0].ip)
                pulumi.export(f"server_{name}_public_ip", curr_server.ipv4_address)

        return program
