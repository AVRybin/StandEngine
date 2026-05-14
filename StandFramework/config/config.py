from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from dataclasses import dataclass

class HCloudSettings(BaseModel):
    token: str

class S3Settings(BaseModel):
    access_key: str
    secret_key: str
    region: str
    endpoint: str
    bucket: str

class ConfigBackend(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    hcloud: HCloudSettings
    s3: S3Settings

@dataclass(kw_only=True)
class StandState:
    owner: str
    passphrase: str
    project: str
    env: str