"""Demo web pública (/demo): probar el modelo sin registro.

- Aislada del panel: no enlaza a /admin ni comparte sesión.
- Controlada por la flag `demo_enabled` (y por `maintenance_mode`), comprobadas en el
  SERVIDOR en cada petición: las flags no son seguridad, aquí sí impiden el uso.
- No guarda nada: la imagen se reenvía a la inferencia y se descarta.
- Límites: tamaño máximo, peticiones por IP y tope global por instancia y hora.
- El navegador pide las detecciones con un umbral bajo y filtra por confianza, aplica
  el NMS y el máximo de detecciones localmente (con los valores de la config, igual
  que la app), así que mover el deslizador no genera peticiones nuevas.
"""

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from ..core.config import get_settings
from ..core.rate_limit import SlidingWindowLimiter
from ..flags.catalog import DEFAULT_CONFIG, SCORE_THRESHOLD_MIN, AppConfig
from ..services import config_store, inference

log = logging.getLogger("ecovision.demo")

DEMO_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(DEMO_DIR / "templates"))

router = APIRouter(prefix="/demo", include_in_schema=False)

# Umbral con el que se consulta al modelo; el filtro real lo hace el deslizador.
# Es el mínimo de score_threshold en el catálogo: el deslizador no baja de ahí.
QUERY_THRESHOLD = SCORE_THRESHOLD_MIN
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}

_settings = get_settings()
_by_ip = SlidingWindowLimiter(_settings.demo_rate_limit, _settings.demo_rate_window_s)
_global = SlidingWindowLimiter(_settings.demo_hourly_limit, 3600)


def _client_ip(request: Request) -> str:
    # uvicorn --proxy-headers ya resuelve X-Forwarded-For en request.client
    return request.client.host if request.client else ""


def _config() -> AppConfig:
    """Config publicada. Si Firestore no responde se usan los valores por defecto,
    que dejan la demo DESACTIVADA (demo_enabled=false)."""
    try:
        return config_store.get_current_cached(get_settings().config_cache_ttl_s).config
    except Exception:  # noqa: BLE001
        log.exception("no se pudo leer la config; la demo queda desactivada")
        return DEFAULT_CONFIG


def _unavailable(cfg: AppConfig) -> Optional[str]:
    """Motivo por el que la demo no puede usarse ahora (None si está disponible)."""
    if cfg.maintenance_mode:
        return cfg.maintenance_message or "Estamos realizando tareas de mantenimiento."
    if not cfg.demo_enabled:
        return "La demo no está disponible en este momento. Vuelve a intentarlo más tarde."
    if not inference.configured():
        return "El servicio de detección no está configurado."
    return None


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status, headers={"Cache-Control": "no-store"})


@router.get("")
@router.get("/")
def demo_page(request: Request):
    cfg = _config()
    reason = _unavailable(cfg)
    ctx = {
        "reason": reason,
        "maintenance": cfg.maintenance_mode,
        "score_threshold": cfg.score_threshold,
        # Parámetros que el navegador aplica igual que la app
        "client_cfg": {
            "score_threshold": cfg.score_threshold,
            "nms_enabled": cfg.nms_enabled,
            "nms_iou": cfg.nms_iou,
            "max_detections": cfg.max_detections,
            "query_threshold": QUERY_THRESHOLD,
            "max_upload_mb": get_settings().demo_max_upload_mb,
        },
    }
    return templates.TemplateResponse(request, "demo.html", ctx, status_code=503 if reason else 200)


@router.get("/estado")
async def demo_status():
    cfg = await run_in_threadpool(_config)
    reason = _unavailable(cfg)
    if reason:
        return JSONResponse({"conectado": False, "disponible": False, "detalle": reason},
                            headers={"Cache-Control": "no-store"})
    data = await inference.health()
    return JSONResponse({**data, "disponible": True}, headers={"Cache-Control": "no-store"})


@router.post("/predict")
async def demo_predict(request: Request, file: UploadFile = File(...)):
    settings = get_settings()
    cfg = await run_in_threadpool(_config)
    reason = _unavailable(cfg)
    if reason:
        return _error(503, reason)

    ip = _client_ip(request)
    wait = _by_ip.blocked_for(ip)
    if wait:
        return _error(429, f"Has hecho muchas pruebas seguidas. Espera {wait} s e inténtalo de nuevo.")
    if _global.blocked_for("all"):
        return _error(429, "La demo recibió muchas pruebas en la última hora. Vuelve a intentarlo más tarde.")

    if (file.content_type or "").lower() not in ALLOWED_TYPES:
        return _error(415, "Formato no admitido. Usa una imagen JPEG, PNG o WebP.")
    max_bytes = int(settings.demo_max_upload_mb * 1024 * 1024)
    data = await file.read(max_bytes + 1)
    if not data:
        return _error(400, "No se recibió ninguna imagen.")
    if len(data) > max_bytes:
        return _error(413, f"La imagen supera {settings.demo_max_upload_mb:g} MB.")

    # Cuenta la petición antes de llamar al modelo (también si falla: cuesta igual)
    _by_ip.hit(ip)
    _global.hit("all")
    try:
        result = await inference.predict(data, file.filename or "captura.jpg",
                                         file.content_type, QUERY_THRESHOLD)
    except inference.InferenceError as exc:
        return _error(exc.status, exc.message)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
