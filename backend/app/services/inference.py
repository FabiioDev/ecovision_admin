"""Cliente del servicio de inferencia (Cloud Run).

Lo usan la demo web (/demo) y la app (POST /api/v1/predict, GET /api/v1/health).
Ni el navegador ni la app hablan directamente con la inferencia: su URL no se expone y
el servicio puede quedarse privado (--no-allow-unauthenticated), llamado con un ID token
de la cuenta de servicio del backend.

Timeouts (cadena coherente, de fuera hacia dentro):
  app            espera request_timeout_s (flag, 5–120 s)
  backend        espera a la inferencia request_timeout_s - MARGEN_S (timeout_predict),
                 con tope INFERENCE_TIMEOUT_S: responde 504 antes de que la app corte
  inferencia     Cloud Run la corta a los 120 s (inference_README.md)
"""

import asyncio
import logging
import threading
import time
from typing import Optional

import httpx
from starlette.concurrency import run_in_threadpool

from ..core.config import get_settings

log = logging.getLogger("ecovision.inference")

# Margen entre lo que espera la app y lo que espera el backend a la inferencia
MARGEN_S = 3.0
# Espera mínima a la inferencia aunque request_timeout_s sea muy bajo
TIMEOUT_MIN_S = 2.0

# /api/v1/health: cuánto espera la respuesta y cuánto se guarda "lista"
HEALTH_ESPERA_S = 2.0
HEALTH_CACHE_S = 15.0

LISTA = "lista"
DESPERTANDO = "despertando"
NO_DISPONIBLE = "no_disponible"
SIN_CONFIGURAR = "sin_configurar"

_client: Optional[httpx.AsyncClient] = None
_token = {"value": None, "expires": 0.0}
_token_lock = threading.Lock()


class InferenceError(Exception):
    """Fallo al llamar a la inferencia. `status` es el código HTTP que se devuelve al cliente."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def configured() -> bool:
    return bool(get_settings().inference_url)


def timeout_predict(request_timeout_s: float) -> float:
    """Espera a la inferencia para una petición cuya app espera `request_timeout_s`."""
    tope = get_settings().inference_timeout_s
    return max(TIMEOUT_MIN_S, min(tope, request_timeout_s - MARGEN_S))


def _base_url() -> str:
    return get_settings().inference_url.rstrip("/")


def _client_get() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=get_settings().inference_timeout_s)
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _auth_headers() -> dict:
    """ID token de Google con audiencia = URL de la inferencia. Dura 1 h; se cachea 50 min.
    Fuera de GCP (sin credenciales de cuenta de servicio) se llama sin token."""
    if not get_settings().inference_use_id_token:
        return {}
    now = time.time()
    with _token_lock:
        if _token["value"] and now < _token["expires"]:
            return {"Authorization": f"Bearer {_token['value']}"}
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2 import id_token

        token = id_token.fetch_id_token(GoogleRequest(), _base_url())
    except Exception as exc:  # noqa: BLE001
        log.warning("sin ID token para la inferencia (%s); se llama sin autenticación", exc)
        return {}
    with _token_lock:
        _token.update(value=token, expires=now + 50 * 60)
    return {"Authorization": f"Bearer {token}"}


async def health(timeout_s: Optional[float] = None) -> dict:
    """{"conectado": bool, "clases": [...]} sin lanzar excepciones."""
    if not configured():
        return {"conectado": False, "detalle": "sin configurar"}
    try:
        headers = await run_in_threadpool(_auth_headers)  # puede consultar el servidor de metadatos
        r = await _client_get().get(f"{_base_url()}/health", headers=headers,
                                    timeout=timeout_s or get_settings().inference_timeout_s)
        r.raise_for_status()
        return {"conectado": True, "clases": r.json().get("clases", [])}
    except httpx.HTTPStatusError as exc:
        return {"conectado": False, "detalle": f"HTTP {exc.response.status_code}"}
    except httpx.HTTPError as exc:
        return {"conectado": False, "detalle": type(exc).__name__}
    except ValueError:
        return {"conectado": False, "detalle": "respuesta no válida"}


# ---- Estado para GET /api/v1/health (despierta la inferencia sin bloquear) ---- #
_estado = {"valor": None, "en": 0.0}
_tarea: Optional[asyncio.Task] = None


async def _comprobar() -> str:
    # Espera larga: un arranque en frío puede tardar; la petición ya lo despierta
    data = await health(get_settings().inference_timeout_s)
    valor = LISTA if data.get("conectado") else NO_DISPONIBLE
    if valor != LISTA:
        log.warning("la inferencia no está disponible: %s", data.get("detalle"))
    _estado.update(valor=valor, en=time.monotonic())
    return valor


async def estado(espera_s: float = HEALTH_ESPERA_S) -> str:
    """Estado de la inferencia para /api/v1/health, en `espera_s` como mucho.

    Lanza (o reutiliza) una comprobación de /health de la inferencia, que de paso la
    despierta. Si no termina a tiempo devuelve DESPERTANDO y la comprobación sigue en
    segundo plano. LISTA se reutiliza HEALTH_CACHE_S segundos sin volver a consultar.
    """
    global _tarea
    if not configured():
        return SIN_CONFIGURAR
    if _estado["valor"] == LISTA and time.monotonic() - _estado["en"] < HEALTH_CACHE_S:
        return LISTA
    if _tarea is None or _tarea.done():
        _tarea = asyncio.create_task(_comprobar())
    try:
        return await asyncio.wait_for(asyncio.shield(_tarea), espera_s)
    except asyncio.TimeoutError:
        return DESPERTANDO


def _reiniciar_estado() -> None:
    """Solo para pruebas."""
    global _tarea
    _estado.update(valor=None, en=0.0)
    _tarea = None


async def predict(data: bytes, filename: str, content_type: str, umbral: float,
                  timeout_s: Optional[float] = None) -> dict:
    """Reenvía la imagen a POST /predict y devuelve su JSON sin modificarlo."""
    if not configured():
        raise InferenceError(503, "El servicio de detección no está configurado.")
    files = {"file": (filename or "captura.jpg", data, content_type or "image/jpeg")}
    headers = await run_in_threadpool(_auth_headers)
    try:
        r = await _client_get().post(f"{_base_url()}/predict", params={"umbral": umbral},
                                     files=files, headers=headers,
                                     timeout=timeout_s or get_settings().inference_timeout_s)
    except httpx.TimeoutException:
        raise InferenceError(504, "El modelo tardó demasiado en responder. Reintenta en unos segundos.")
    except httpx.HTTPError as exc:
        log.exception("fallo al llamar a la inferencia")
        raise InferenceError(502, f"No se pudo contactar el modelo ({type(exc).__name__}).")

    if r.status_code >= 400:
        detail = "El modelo rechazó la imagen."
        try:
            detail = r.json().get("detail", detail)
        except ValueError:
            pass
        if r.status_code in (401, 403):
            # Cloud Run rechazó al backend: permisos (roles/run.invoker), no culpa del usuario
            log.error("la inferencia respondió %s: revisa roles/run.invoker", r.status_code)
            raise InferenceError(502, "No se pudo contactar el modelo.")
        if r.status_code >= 500:
            log.error("la inferencia falló con HTTP %s: %s", r.status_code, detail)
            raise InferenceError(502, "El modelo falló al procesar la imagen. Reintenta en unos segundos.")
        # 4xx de la inferencia (imagen ilegible, demasiado grande...): se pasa tal cual
        raise InferenceError(r.status_code, str(detail))
    try:
        return r.json()
    except ValueError:
        raise InferenceError(502, "La respuesta del modelo no es válida.")
