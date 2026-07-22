from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class StandSettings(BaseModel):
    user: str
    passphrase: str
    path_to_key: Path
    path_to_configset: Path


class OutputSettings(BaseModel):
    console: bool = True
    console_secrets: bool = False
    file: bool = False
    file_path: Path | None = None

    @model_validator(mode="after")
    def validate_file_path(self):
        if self.file and self.file_path is None:
            raise ValueError("OUTPUT__FILE_PATH is required when OUTPUT__FILE=true")
        if (
            self.file
            and self.file_path is not None
            and self.file_path.exists()
            and not self.file_path.is_dir()
        ):
            raise ValueError("OUTPUT__FILE_PATH must point to a directory")
        return self

class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    stand: StandSettings
    output: OutputSettings = Field(default_factory=OutputSettings)
