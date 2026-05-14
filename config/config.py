from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class StandSettings(BaseModel):
    user: str
    passphrase: str
    project_name: str
    name: str

class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    stand: StandSettings