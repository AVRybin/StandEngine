import io
from pathlib import Path

import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

class SShKey:
    BASE_DIR = Path(__file__).resolve().parent.parent

    @staticmethod
    def get_public_key_from_private(private_key: str) -> str:
        private_key = serialization.load_ssh_private_key(
            private_key.encode("utf-8"),
            password=None,
        )
        public_key = private_key.public_key()
        public_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        )

        return public_bytes.decode("utf-8")

    @staticmethod
    def get_paramiko_key(private_key: str) -> paramiko.PKey:
        for key_class in (paramiko.Ed25519Key, paramiko.RSAKey):
            try:
                key_file = io.StringIO(private_key)
                return key_class.from_private_key(key_file)
            except paramiko.SSHException:
                pass

        raise ValueError("Неподдерживаемый тип приватного ключа")

    @staticmethod
    def generate_ssh_key(key_type: str = "ed25519", key_size: int = 3072) -> tuple[str, str]:
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
