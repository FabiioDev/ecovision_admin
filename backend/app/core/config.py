"""Configuración del servicio leída de variables de entorno (o de backend/.env en local)."""

from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Qué routers se montan: permite separar el panel en otro servicio con la misma imagen
    role: Literal["all", "api", "admin"] = "all"

    # Proyecto de GCP/Firebase. En Cloud Run se puede omitir (se detecta por ADC).
    google_cloud_project: Optional[str] = None

    # Firma de la cookie de sesión del panel. Obligatoria si se monta el panel.
    secret_key: str = ""

    cookie_secure: bool = True
    session_max_age_s: int = Field(28800, ge=300)

    login_max_attempts: int = Field(5, ge=1)
    login_window_s: int = Field(900, ge=60)

    config_cache_ttl_s: int = Field(30, ge=0)
    config_require_auth: bool = False

    docs_enabled: bool = False
    log_level: str = "INFO"

    @property
    def api_enabled(self) -> bool:
        return self.role in ("all", "api")

    @property
    def admin_enabled(self) -> bool:
        return self.role in ("all", "admin")


@lru_cache
def get_settings() -> Settings:
    return Settings()
