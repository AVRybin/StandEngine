from dataclasses import dataclass
from typing import Callable

import pulumi
import pulumi_hcloud as hcloud

from server_designer.server import Server

@dataclass(kw_only=True)
class ServersDesigner:
    ssh_admin_name: str
    user_data: str

    def get_program(self, servers: dict[str, Server]) -> Callable[[], None]:
        def program() -> None:
            map_network = {}
            user_data = self.user_data

            for name, server in servers.items():
                if server.network not in map_network:
                    target_network = hcloud.get_network(name=server.network)
                    map_network[server.network] = target_network.id

                network_args = {
                    "network_id": map_network[server.network],
                }

                curr_server = hcloud.Server(
                    name,
                    name=name,
                    image=server.image,
                    server_type=server.type,
                    location=server.location,
                    ssh_keys=[self.ssh_admin_name],
                    networks=[network_args],
                    user_data=user_data,
                    opts=pulumi.ResourceOptions(
                        ignore_changes=["user_data"],
                    ),
                )

                pulumi.export(f"server_{name}_internal_ip", curr_server.networks[0].ip)

        return program