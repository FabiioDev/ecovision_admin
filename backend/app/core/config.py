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

    # --- Servicio de inferencia (demo y /api/v1/predict) ---
    # URL base sin barra final, p. ej. https://ecovision-api-xxxx-uc.a.run.app
    inference_url: str = ""
    # Enviar un ID token de Google (audiencia = inference_url). Necesario cuando la
    # inferencia es privada (--no-allow-unauthenticated); con el servicio público no estorba.
    inference_use_id_token: bool = True
    # TOPE de espera a la inferencia. La espera real de cada /predict sale de la flag
    # request_timeout_s (services.inference.timeout_predict): request_timeout_s - 3, para
    # responder antes que la app. 115 < 120 s, el timeout del servicio de inferencia.
    inference_timeout_s: float = Field(115, gt=0, le=300)

    # --- POST /api/v1/predict (app) ---
    # La app envía JPEG 640x640 (< 1 MB); el margen cubre imágenes mayores.
    predict_max_upload_mb: float = Field(5, gt=0, le=10)
    # Peticiones por usuario (uid) en la ventana. Por instancia (core/rate_limit.py).
    predict_rate_limit: int = Field(20, ge=1)
    predict_rate_window_s: int = Field(60, ge=10)

    # --- Demo web pública (/demo) ---
    demo_max_upload_mb: float = Field(5, gt=0, le=10)
    # Peticiones de análisis por IP en la ventana (la app de demo filtra el umbral en el
    # navegador, así que mover el deslizador no gasta peticiones)
    demo_rate_limit: int = Field(10, ge=1)
    demo_rate_window_s: int = Field(60, ge=10)
    # Tope global por instancia y hora: limita el gasto aunque lleguen muchas IP distintas
    demo_hourly_limit: int = Field(300, ge=1)

    @property
    def api_enabled(self) -> bool:
        return self.role in ("all", "api")

    @property
    def demo_mounted(self) -> bool:
        """La demo es pública como la API: se monta con ROLE=all o ROLE=api.
        Además debe estar activada con la flag `demo_enabled` del panel."""
        return self.role in ("all", "api")

    @property
    def admin_enabled(self) -> bool:
        return self.role in ("all", "admin")


@lru_cache
def get_settings() -> Settings:
    return Settings()
