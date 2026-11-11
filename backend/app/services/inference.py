"""Cliente del servicio de inferencia (Cloud Run).

Hoy lo usa la demo web (/demo). En la Fase 4 lo reutilizará POST /api/v1/predict.
El navegador nunca habla directamente con la inferencia: la URL del modelo no se
expone y el servicio podrá quedarse privado (--no-allow-unauthenticated), llamado
con un ID token de la cuenta de servicio del backend.
"""

import logging
import threading
import time
from typing import Optional

import httpx
from starlette.concurrency import run_in_threadpool

from ..core.config import get_settings

log = logging.getLogger("ecovision.inference")

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


async def health() -> dict:
    """{"conectado": bool, "clases": [...]} sin lanzar excepciones."""
    if not configured():
        return {"conectado": False, "detalle": "sin configurar"}
    try:
        headers = await run_in_threadpool(_auth_headers)  # puede consultar el servidor de metadatos
        r = await _client_get().get(f"{_base_url()}/health", headers=headers)
        r.raise_for_status()
        return {"conectado": True, "clases": r.json().get("clases", [])}
    except httpx.HTTPStatusError as exc:
        return {"conectado": False, "detalle": f"HTTP {exc.response.status_code}"}
    except httpx.HTTPError as exc:
        return {"conectado": False, "detalle": type(exc).__name__}


async def predict(data: bytes, filename: str, content_type: str, umbral: float) -> dict:
    """Reenvía la imagen a POST /predict y devuelve su JSON sin modificarlo."""
    if not configured():
        raise InferenceError(503, "El servicio de detección no está configurado.")
    files = {"file": (filename or "captura.jpg", data, content_type or "image/jpeg")}
    headers = await run_in_threadpool(_auth_headers)
    try:
        r = await _client_get().post(f"{_base_url()}/predict", params={"umbral": umbral},
                                     files=files, headers=headers)
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
        # 401/403 de Cloud Run = problema de permisos del backend, no del usuario
        status = 502 if r.status_code in (401, 403) else r.status_code
        if status == 502:
            log.error("la inferencia respondió %s: revisa roles/run.invoker", r.status_code)
            detail = "No se pudo contactar el modelo."
        raise InferenceError(status, str(detail))
    return r.json()
