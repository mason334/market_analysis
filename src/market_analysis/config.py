from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).parent.parent.parent  # project root


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    # Database — signals (market_analysis)
    db_host: str = Field(default="localhost")
    db_port: int = Field(default=5432)
    db_name: str = Field(default="market_analysis")
    db_user: str = Field(default="postgres")
    db_password: str = Field(default="")

    # Source database — OHLCV (market_data); defaults to same host/user/password
    source_db_name: str = Field(default="market_data")

    # Indicator params — loaded from settings.yaml
    indicators: dict[str, Any] = Field(default_factory=dict)

    # Strategy params — loaded from settings.yaml. Kept for backward-compatible config.
    strategies: dict[str, Any] = Field(default_factory=dict)

    # Pipeline
    pipeline: dict[str, Any] = Field(default_factory=dict)

    # Read-only validation/reporting workflows.
    validation: dict[str, Any] = Field(default_factory=dict)

    @property
    def dsn(self) -> str:
        return (
            f"host={self.db_host} port={self.db_port} "
            f"dbname={self.db_name} user={self.db_user} password={self.db_password}"
        )

    @property
    def source_dsn(self) -> str:
        return (
            f"host={self.db_host} port={self.db_port} "
            f"dbname={self.source_db_name} user={self.db_user} password={self.db_password}"
        )


def _load_yaml(path: Path) -> dict[str, Any]:
    if path.exists():
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def get_settings(settings_yaml: Path | None = None) -> Settings:
    yaml_path = settings_yaml or (_ROOT / "config" / "settings.yaml")
    yaml_data = _load_yaml(yaml_path)
    return Settings(**yaml_data)


# Module-level singleton
settings = get_settings()
