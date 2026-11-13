"""Rutas /api/v1 consumidas por la app Android. Contrato: docs/openapi.yaml."""

import logging
import math
import time
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel

from .. import __version__
from ..core.config import get_settings
from ..core.firebase import bearer_scheme, verify_app_user
from ..core.rate_limit import SlidingWindowLimiter
from ..core.uploads import ImagenInvalida, leer_imagen
from ..flags.catalog import DEFAULT_CONFIG, SCORE_THRESHOLD_MIN, AppConfig
from ..services import config_store, inference

log = logging.getLogger("ecovision.api")

router = APIRouter(prefix="/api/v1", tags=["app"])


class Health(BaseModel):
    status: str
    service: str
    version: str
    inferencia: Literal["lista", "despertando", "no_disponible", "sin_configurar"]


class PublicConfig(BaseModel):
    config_version: int
    maintenance_mode: bool
    maintenance_message: str
    min_app_version: str


class ConfigResponse(BaseModel):
    config_version: int
    updated_at: Optional[datetime]
    config: AppConfig


def _snapshot() -> config_store.ConfigSnapshot:
    try:
        return config_store.get_current_cached(get_settings().config_cache_ttl_s)
    except Exception:  # noqa: BLE001
        log.exception("no se pudo leer la config de Firestore")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "config no disponible")


def _config_auth(creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)) -> None:
    """En la Fase 1 la config es pública (no contiene secretos). Con
    CONFIG_REQUIRE_AUTH=true (Fase 3) se exige el ID token de Firebase."""
    if get_settings().config_require_auth:
        verify_app_user(creds)


@router.get("/health", response_model=Health)
async def health(response: Response):
    """Responde en ~2 s como máximo y despierta la inferencia (arranque en frío)."""
    response.headers["Cache-Control"] = "no-store"
    return Health(status="ok", service="backend", version=__version__,
                  inferencia=await inference.estado())


@router.get("/config/public", response_model=PublicConfig)
def config_public(response: Response):
    snap = _snapshot()
    response.headers["Cache-Control"] = "no-cache"
    c = snap.config
    return PublicConfig(config_version=snap.version, maintenance_mode=c.maintenance_mode,
                        maintenance_message=c.maintenance_message, min_app_version=c.min_app_version)


@router.get("/config", response_model=ConfigResponse, dependencies=[Depends(_config_auth)],
            responses={304: {"description": "Sin cambios (If-None-Match coincide)"}})
def config(request: Request):
    snap = _snapshot()
    etag = f'"v{snap.version}"'
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    inm = request.headers.get("if-none-match", "")
    if etag in [t.strip().removeprefix("W/") for t in inm.split(",")]:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    body = ConfigResponse(config_version=snap.version, updated_at=snap.updated_at, config=snap.config)
    return Response(content=body.model_dump_json(), media_type="application/json", headers=headers)


# ---------------------------------------------------------------------------
# POST /api/v1/predict — proxy hacia la inferencia (Fase 4)
# ---------------------------------------------------------------------------

_settings = get_settings()
_por_usuario = SlidingWindowLimiter(_settings.predict_rate_limit, _settings.predict_rate_window_s)


def _error(code: int, message: str, headers: Optional[dict] = None) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=code,
                        headers={"Cache-Control": "no-store", **(headers or {})})


def _config_predict() -> AppConfig:
    """Config publicada; si Firestore no responde (y no hay copia), los valores por
    defecto: la detección sigue funcionando con los parámetros de siempre."""
    try:
        return config_store.get_current_cached(get_settings().config_cache_ttl_s).config
    except Exception:  # noqa: BLE001
        log.exception("no se pudo leer la config; /predict usa los valores por defecto")
        return DEFAULT_CONFIG


def _umbral(valor: Optional[float]) -> float:
    if valor is None or math.isnan(valor):
        return SCORE_THRESHOLD_MIN
    return min(1.0, max(SCORE_THRESHOLD_MIN, valor))


@router.post("/predict")
async def predict(
    file: UploadFile = File(...),
    umbral: Optional[float] = Query(None, description="Confianza mínima; se recorta a [0.3, 1]"),
    user: dict = Depends(verify_app_user),
):
    """Reenvía la imagen a la inferencia y devuelve su respuesta sin cambios.
    No guarda ni registra la imagen."""
    settings = get_settings()
    uid = user.get("uid", "")
    cfg = await run_in_threadpool(_config_predict)
    if cfg.maintenance_mode:
        return _error(503, cfg.maintenance_message
                      or "Estamos realizando tareas de mantenimiento.")

    espera = _por_usuario.blocked_for(uid)
    if espera:
        return _error(429, f"Has hecho muchas detecciones seguidas. Espera {espera} s e inténtalo de nuevo.",
                      {"Retry-After": str(espera)})

    try:
        data = await leer_imagen(file, settings.predict_max_upload_mb)
    except ImagenInvalida as exc:
        return _error(exc.status, exc.message)

    # Cuenta antes de llamar al modelo (también si falla: cuesta igual)
    _por_usuario.hit(uid)
    inicio = time.monotonic()
    try:
        result = await inference.predict(data, file.filename or "captura.jpg", file.content_type,
                                         _umbral(umbral), inference.timeout_predict(cfg.request_timeout_s))
    except inference.InferenceError as exc:
        log.warning("predict uid=%s falló: HTTP %s", uid, exc.status)
        return _error(exc.status, exc.message)
    log.info("predict uid=%s ok en %.0f ms (%d detecciones)", uid,
             (time.monotonic() - inicio) * 1000, len(result.get("detecciones", []) or []))
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


# FUTURO (Fase 5) — `cloud_sync_enabled`: las rutas de imágenes deberán rechazar
# la subida cuando la flag esté desactivada (las flags no son seguridad):
#
# @router.post("/images", dependencies=[Depends(verify_app_user)])
# def create_image(...):
#     if not _snapshot().config.cloud_sync_enabled:
#         raise HTTPException(status.HTTP_403_FORBIDDEN, "sincronización desactivada")
#     ...  # crear metadatos en `images` y devolver URL firmada V4 de subida
