import os
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Server settings
    # Botkeep sets SERVER_PORT; PORT is standard fallback
    SERVER_PORT: int = int(os.getenv("SERVER_PORT", os.getenv("PORT", "8000")))
    HOST: str = "0.0.0.0"

    # CORS settings: comma-separated list of origins
    FRONTEND_ORIGINS: str = os.getenv("FRONTEND_ORIGINS", "*")

    # Download limits
    MAX_FILE_SIZE_MB: int = 500
    DOWNLOAD_TIMEOUT_SECONDS: int = 600
    DOWNLOAD_TTL_SECONDS: int = 900  # 15 minutes file retention
    MAX_CONCURRENT_DOWNLOADS: int = 3
    TEMP_DOWNLOAD_DIR: str = os.getenv("TEMP_DOWNLOAD_DIR", "downloads_temp")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def allowed_origins(self) -> List[str]:
        if not self.FRONTEND_ORIGINS or self.FRONTEND_ORIGINS.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.FRONTEND_ORIGINS.split(",") if origin.strip()]

    @property
    def temp_dir_path(self) -> Path:
        path = Path(self.TEMP_DOWNLOAD_DIR)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent / path
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
