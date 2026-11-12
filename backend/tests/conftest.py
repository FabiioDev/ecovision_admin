"""Pruebas del backend sin GCP: Firestore, Firebase Auth y la inferencia se simulan.

Ejecutar desde backend/:
    pip install -r requirements-dev.txt
    python -m pytest
"""

import os
import sys
from pathlib import Path

import httpx
import pytest

# Variables ANTES de importar la app (get_settings() se cachea). Se fijan todas las que
# influyen en las pruebas para no depender de backend/.env.
os.environ.update({
    "ROLE": "api",
    "GOOGLE_CLOUD_PROJECT": "proyecto-de-prueba",
    "CONFIG_REQUIRE_AUTH": "false",
    "CONFIG_CACHE_TTL_S": "0",
    "DOCS_ENABLED": "false",
    "INFERENCE_URL": "http://inferencia.test",
    "INFERENCE_USE_ID_TOKEN": "false",
    "INFERENCE_TIMEOUT_S": "115",
    "PREDICT_MAX_UPLOAD_MB": "1",
    "PREDICT_RATE_LIMIT": "3",
    "PREDICT_RATE_WINDOW_S": "60",
    "DEMO_MAX_UPLOAD_MB": "1",
    "DEMO_RATE_LIMIT": "50",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from firebase_admin import auth as fb_auth  # noqa: E402

from app.core import firebase  # noqa: E402
from app.flags.catalog import AppConfig  # noqa: E402
from app.main import app  # noqa: E402
from app.services import config_store, inference  # noqa: E402

# Respuesta de ejemplo de la inferencia (contrato: #/components/schemas/Prediccion)
PREDICCION = {
    "ancho": 640, "alto": 640, "umbral": 0.3, "latencia_ms": 812.4,
    "detecciones": [{"categoria": "Metal", "clase_id": 4, "confianza": 0.98,
                     "bbox_norm": [0.1, 0.2, 0.5, 0.6], "bbox_px": [64, 128, 320, 384]}],
}

# Tokens de prueba → resultado de verify_id_token
TOKEN_OK = "token-ok"
TOKEN_DESACTIVADO = "token-desactivado"
TOKEN_CADUCADO = "token-caducado"
TOKEN_REVOCADO = "token-revocado"


def _verify_id_token(token, app=None, check_revoked=False):
    if token.startswith(TOKEN_OK):
        # "token-ok-<uid>" permite simular varios usuarios
        return {"uid": token[len(TOKEN_OK) + 1:] or "usuario-1"}
    if token == TOKEN_DESACTIVADO:
        raise fb_auth.UserDisabledError("desactivado")
    if token == TOKEN_CADUCADO:
        raise fb_auth.ExpiredIdTokenError("caducado", None)
    if token == TOKEN_REVOCADO:
        raise fb_auth.RevokedIdTokenError("revocado")
    raise fb_auth.InvalidIdTokenError("inválido")


@pytest.fixture(autouse=True)
def _sin_gcp(monkeypatch):
    """Firebase Auth simulado y config por defecto en cada prueba."""
    monkeypatch.setattr(firebase, "get_firebase_app", lambda: None)
    monkeypatch.setattr(firebase.auth, "verify_id_token", _verify_id_token)
    usar_config(monkeypatch)
    inference._reiniciar_estado()
    # Los limitadores son de módulo: cada prueba empieza sin peticiones contadas
    from app.api import routes
    from app.demo import routes as demo_routes
    for limitador in (routes._por_usuario, demo_routes._by_ip, demo_routes._global):
        limitador._events.clear()
    yield
    inference._reiniciar_estado()


def usar_config(monkeypatch, version=1, **valores):
    """La config publicada pasa a ser AppConfig(**valores)."""
    snap = config_store.ConfigSnapshot(AppConfig(**valores), version, None, None)
    monkeypatch.setattr(config_store, "get_current_cached", lambda ttl_s: snap)
    return snap


class InferenciaFalsa:
    """Sustituye la conexión HTTP con la inferencia (httpx.MockTransport).

    `respuesta` puede ser un httpx.Response, una excepción de httpx (se lanza) o una
    función async request -> Response. Guarda las peticiones y sus timeouts.
    """

    def __init__(self):
        self.peticiones: list[httpx.Request] = []
        self.timeouts: list[float] = []
        self.respuesta = httpx.Response(200, json=PREDICCION)
        self.salud = httpx.Response(200, json={"status": "ok", "clases": ["Metal"]})

    async def _manejar(self, request: httpx.Request) -> httpx.Response:
        self.peticiones.append(request)
        self.timeouts.append(request.extensions.get("timeout", {}).get("read"))
        r = self.salud if request.url.path == "/health" else self.respuesta
        if isinstance(r, Exception):
            raise r
        if callable(r):
            return await r(request)
        return r

    @property
    def predicciones(self) -> list[httpx.Request]:
        return [p for p in self.peticiones if p.url.path == "/predict"]


@pytest.fixture
def inferencia(monkeypatch):
    falsa = InferenciaFalsa()
    monkeypatch.setattr(inference, "_client",
                        httpx.AsyncClient(transport=httpx.MockTransport(falsa._manejar)))
    yield falsa
    monkeypatch.setattr(inference, "_client", None)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def auth(token=TOKEN_OK):
    return {"Authorization": f"Bearer {token}"}


def jpeg(tamano=1000):
    """Bytes con el tipo declarado image/jpeg (la inferencia está simulada)."""
    return {"file": ("captura.jpg", b"\xff\xd8" + b"0" * tamano, "image/jpeg")}
