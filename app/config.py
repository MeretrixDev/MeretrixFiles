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

    rate_limit_enabled: bool = True
    rate_login_per_minute: int = 10
    rate_register_per_hour: int = 10
    rate_upload_per_minute: int = 30

    storage_dir: Path = Path("storage")

    max_upload_size: int = 500 * 1024 * 1024 #500MB
    chunk_size: int = 1024 * 1024 #1MB

    frame_size: int = Field(1024 * 1024, ge=64 * 1024, le=64 * 1024 * 1024)

    user_quota_bytes: int = Field(5 * 1024**3, ge=0)
    anon_ttl_hours: int = Field(24, ge=1, le=24 * 365)
    anon_max_upload_size: int = Field(100 * 1024**2, ge=1)
    max_files_per_upload: int = Field(50, ge=1, le=1000)

    compress_level: int = 3

    database_url: str = "sqlite:///./meretrixfiles.db"

    base_url: str = "http://localhost:8000"

    cleanup_in_app: bool = True
    cleanup_interval_seconds: int = Field(600, ge=10)

    @property
    def temp_dir(self) -> Path:
        return self.storage_dir / "temp"

    def ensure_dirs(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

@lru_cache
def get_settings() -> Settings:
    return Settings()

