from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MRTRXFILES_",
        extra="ignore",
    )

    secret_key: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_expires_in_minutes: int = 60

    storage_dir: Path = Path("storage")

    max_upload_size: int = 5 * 1024 * 1024 #500MB
    chunk_size: int = 1024 * 1024 #1MB

    compress_level: int = 3

    database_url: str = "sqlite:///./meretrixfiles.db"

    base_url: str = "http://localhost:8000"

    @property
    def temp_dir(self) -> Path:
        return self.storage_dir / "temp"

    def ensure_dirs(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

@lru_cache
def get_settings() -> Settings:
    return Settings()