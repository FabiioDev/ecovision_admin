"""Descarga pública del APK de EcoVision SV (/descargar).

- Aislada del panel como la demo: no enlaza a /admin ni comparte sesión.
- Controlada por la flag `download_enabled` (apagada por defecto), comprobada en el
  SERVIDOR en cada petición. El APK vive en GitHub Releases (público): apagar la flag
  quita el enlace del backend, pero no impide descargarlo a quien ya tenga la URL.
- /descargar/apk solo redirige a `apk_url`, que el catálogo restringe a enlaces de
  descarga de releases de github.com (no es una redirección abierta). Límite por IP.
- Durante el mantenimiento se puede descargar: lo que se bloquea es la detección.
"""

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..core.config import get_settings
from ..core.rate_limit import SlidingWindowLimiter
from ..flags.catalog import DEFAULT_CONFIG, AppConfig
from ..services import config_store

log = logging.getLogger("ecovision.descarga")

DESCARGA_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(DESCARGA_DIR / "templates"))

router = APIRouter(prefix="/descargar", include_in_schema=False)

_settings = get_settings()
_por_ip = SlidingWindowLimiter(_settings.download_rate_limit, _settings.download_rate_window_s)


def _client_ip(request: Request) -> str:
    # uvicorn --proxy-headers ya resuelve X-Forwarded-For en request.client
    return request.client.host if request.client else ""


def _config() -> AppConfig:
    """Config publicada. Si Firestore no responde se usan los valores por defecto, que
    dejan la descarga DESACTIVADA (download_enabled=false)."""
    try:
        return config_store.get_current_cached(get_settings().config_cache_ttl_s).config
    except Exception:  # noqa: BLE001
        log.exception("no se pudo leer la config; la descarga queda desactivada")
        return DEFAULT_CONFIG


def _no_disponible(cfg: AppConfig) -> Optional[str]:
    """Motivo por el que no se puede descargar ahora (None si se puede)."""
    if not cfg.download_enabled or not cfg.apk_url:
        return "La descarga de la app no está disponible en este momento. Vuelve a intentarlo más tarde."
    return None


def _pagina(request: Request, cfg: AppConfig, aviso: Optional[str] = None, status_code: int = 200):
    motivo = _no_disponible(cfg)
    ctx = {
        "motivo": motivo,
        "aviso": aviso,
        "version": cfg.apk_version,
        "sha256": cfg.apk_sha256,
        "mantenimiento": cfg.maintenance_mode,
        "mensaje_mantenimiento": cfg.maintenance_message,
    }
    if motivo and status_code == 200:
        status_code = 503
    return templates.TemplateResponse(request, "descarga.html", ctx, status_code=status_code,
                                      headers={"Cache-Control": "no-cache"})


@router.get("")
@router.get("/")
async def pagina_descarga(request: Request):
    return _pagina(request, await run_in_threadpool(_config))


@router.get("/apk")
async def descargar_apk(request: Request):
    cfg = await run_in_threadpool(_config)
    if _no_disponible(cfg):
        return RedirectResponse("/descargar", status_code=303)

    ip = _client_ip(request)
    espera = _por_ip.blocked_for(ip)
    if espera:
        resp = _pagina(request, cfg, aviso=f"Has pedido la descarga muchas veces seguidas. "
                                          f"Espera {espera} s e inténtalo de nuevo.", status_code=429)
        resp.headers["Retry-After"] = str(espera)
        return resp

    _por_ip.hit(ip)
    log.info("descarga del APK %s", cfg.apk_version or "(sin versión)")
    return RedirectResponse(cfg.apk_url, status_code=302, headers={"Cache-Control": "no-store"})
