from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from mako.template import Template


class BaseHelpers:
    BASE_DIR = Path(__file__).resolve().parent.parent
    CLOUD_INIT_TEMPLATE = BASE_DIR / "templates" / "cloud-init.yaml.mako"

    @staticmethod
    def generate_ssh_key(key_type: str = "rsa", key_size: int = 3072) -> tuple[str, str]:
        if key_type == "rsa":
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=key_size,
            )
        elif key_type == "ed25519":
            private_key = ed25519.Ed25519PrivateKey.generate()
        else:
            raise ValueError(f"Неподдерживаемый тип ключа: {key_type}")

        private_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.OpenSSH,
            encryption_algorithm=serialization.NoEncryption(),
        )

        public_key = private_key.public_key()
        public_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        )

        return private_bytes.decode("utf-8"), public_bytes.decode("utf-8")

    @staticmethod
    def render_cloud_init(user_admin: str, ssh_public_key: str, user_app: str) -> str:
        template = Template(filename=str(BaseHelpers.CLOUD_INIT_TEMPLATE))
        return template.render(
            user_admin=user_admin,
            ssh_public_key=ssh_public_key,
            user_app=user_app,
        )
