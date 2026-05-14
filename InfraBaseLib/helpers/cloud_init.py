from pathlib import Path
from mako.template import Template

class CloudInit:
    BASE_DIR = Path(__file__).resolve().parent.parent
    CLOUD_INIT_TEMPLATE = BASE_DIR / "templates" / "cloud-init.yaml.mako"

    @staticmethod
    def render(user_admin: str, ssh_public_key: str, user_app: str) -> str:
        template = Template(filename=str(CloudInit.CLOUD_INIT_TEMPLATE))
        return template.render(
            user_admin=user_admin,
            ssh_public_key=ssh_public_key,
            user_app=user_app,
        )
