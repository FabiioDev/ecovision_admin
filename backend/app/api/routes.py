"""Rutas /api/v1 consumidas por la app Android. Contrato: docs/openapi.yaml."""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel

from .. import __version__
from ..core.config import get_settings
from ..core.firebase import bearer_scheme, verify_app_user
from ..flags.catalog import AppConfig
from ..services import config_store

log = logging.getLogger("ecovision.api")

router = APIRouter(prefix="/api/v1", tags=["app"])


class Health(BaseModel):
    status: str
    service: str
    version: str


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
def health():
    # FUTURO (Fase 4): hacer ping a /health de `inference` para despertarlo.
    return Health(status="ok", service="backend", version=__version__)


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


# FUTURO (Fase 5) — `cloud_sync_enabled`: las rutas de imágenes deberán rechazar
# la subida cuando la flag esté desactivada (las flags no son seguridad):
#
# @router.post("/images", dependencies=[Depends(verify_app_user)])
# def create_image(...):
#     if not _snapshot().config.cloud_sync_enabled:
#         raise HTTPException(status.HTTP_403_FORBIDDEN, "sincronización desactivada")
#     ...  # crear metadatos en `images` y devolver URL firmada V4 de subida
