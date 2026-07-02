from pathlib import Path
from mako.template import Template

class CloudInit:
    @staticmethod
    def render(
        user_admin: str,
        ssh_public_key: str,
        user_app: str,
        template_path: Path,
        network_ip_range: str,
    ) -> str:
        template = Template(filename=str(template_path))
        return template.render(
            user_admin=user_admin,
            ssh_public_key=ssh_public_key,
            user_app=user_app,
            network_ip_range=network_ip_range,
        )
