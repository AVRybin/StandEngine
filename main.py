from helpers.base_helpers import BaseHelpers
from config.config import Config
from metal_provision.provision import MetalProvision

from server_designer.designer import ServersDesigner
from server_designer.server import Server

config = Config()

priv, pub = BaseHelpers.generate_ssh_key()
print(priv)
print(pub)

#pub = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDQw07Filf8ik2UOZbCpi1TJV+QG8snQFmABrQcrl70GYFFSK7n9VXkJqkdWEqzG6t2F3v9Oe/vWqKE+EVlBUwaayXVuXNoLD6u3bsx6lX1bbZeYPNDDtIa/x/la6Gk7g69EHaGbGucI6FnDjUZVMzinprOkBbtkL5bPH/cQ0YFTOVcn4d8RM3WcxeN07n9TJsKimbb1g3MMi59rnd9P0AzzIIje18Ko4LAK06Q3Ng0Z0Ku2PJQ5AEPsx96L6IjJgg6GvC/C0XFRfVPilzWAPoJwWd3T/ZWbf0ZDC+wIwJJ2xnmwI3xDvLa6fL4lDZThIgZmbwTcjggqhLe35djft43CX2qQWkKVZNDLbygyKY72DO/AsWEUzqtewwX1+PXPJpzF0tJc/q95csXSpilgJD5jeolgppUzMdWrYFqJrpS54clJIGZdY1bKzfiNaHp5eysaHvydwpyriCZyScimPiI57fHO14l1JQhISn6YXbnlBuzadIVCO5WM1QjUCO5Lf8="
cloud_init = BaseHelpers.render_cloud_init("av.rybin", pub, "userapp")

provision = MetalProvision(
    s3_bucket=config.s3.bucket,
    s3_region=config.s3.region,
    s3_endpoint=config.s3.endpoint,
    passphrase=config.stand.passphrase,
    s3_access_key=config.s3.access_key,
    s3_secret_key=config.s3.secret_key,
    stand_name=config.stand.name,
    project_name=config.stand.project_name,
    user_name=config.stand.user,
    provider_token=config.hcloud.token,
)

designer = ServersDesigner(
    ssh_admin_name="AVRybin",
    user_data=cloud_init,
)

servers = {
    "test-server": Server(
        location="hel1",
        type="cpx32",
        image="357704314",
        network="network-p2p",
    )
}

program_for_provision = designer.get_program(servers)

result = provision.destroy(program_for_provision)
#print(result.outputs.get("server_test-server_internal_ip"))
