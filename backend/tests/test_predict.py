"""POST /api/v1/predict (contrato: docs/openapi.yaml)."""

import httpx
import pytest

from app.services import config_store, inference
from conftest import (PREDICCION, TOKEN_CADUCADO, TOKEN_DESACTIVADO, TOKEN_OK, TOKEN_REVOCADO,
                      auth, jpeg, usar_config)

URL = "/api/v1/predict"


# ---- autenticación ---- #

def test_sin_token_401(client, inferencia):
    r = client.post(URL, files=jpeg())
    assert r.status_code == 401
    assert inferencia.peticiones == []


@pytest.mark.parametrize("token, codigo", [
    (TOKEN_CADUCADO, 401), (TOKEN_REVOCADO, 401), ("basura", 401), (TOKEN_DESACTIVADO, 403),
])
def test_token_no_valido(client, inferencia, token, codigo):
    r = client.post(URL, files=jpeg(), headers=auth(token))
    assert r.status_code == codigo
    assert "detail" in r.json()
    assert inferencia.peticiones == []


# ---- caso normal ---- #

def test_devuelve_la_respuesta_de_la_inferencia_sin_cambios(client, inferencia):
    r = client.post(URL, files=jpeg(), params={"umbral": 0.45}, headers=auth())
    assert r.status_code == 200
    assert r.json() == PREDICCION
    assert r.headers["cache-control"] == "no-store"
    (p,) = inferencia.predicciones
    assert p.url.params["umbral"] == "0.45"
    assert b"captura.jpg" in p.content and b"image/jpeg" in p.content


@pytest.mark.parametrize("enviado, esperado", [
    (None, "0.3"), ("0.1", "0.3"), ("5", "1.0"), ("nan", "0.3"), ("0.3", "0.3"),
])
def test_umbral_recortado(client, inferencia, enviado, esperado):
    params = {} if enviado is None else {"umbral": enviado}
    r = client.post(URL, files=jpeg(), params=params, headers=auth())
    assert r.status_code == 200
    assert inferencia.predicciones[0].url.params["umbral"] == esperado


def test_timeout_sale_de_request_timeout_s(client, inferencia, monkeypatch):
    usar_config(monkeypatch, request_timeout_s=30)
    client.post(URL, files=jpeg(), headers=auth())
    assert inferencia.timeouts[-1] == 27  # 30 - 3: responde antes que la app


def test_sin_config_usa_los_valores_por_defecto(client, inferencia, monkeypatch):
    def falla(ttl_s):
        raise RuntimeError("Firestore no responde")
    monkeypatch.setattr(config_store, "get_current_cached", falla)
    r = client.post(URL, files=jpeg(), headers=auth())
    assert r.status_code == 200
    assert inferencia.timeouts[-1] == 57  # request_timeout_s por defecto (60) - 3


# ---- flags que se imponen en el servidor ---- #

def test_mantenimiento_503_con_el_mensaje(client, inferencia, monkeypatch):
    usar_config(monkeypatch, maintenance_mode=True, maintenance_message="Volvemos pronto")
    r = client.post(URL, files=jpeg(), headers=auth())
    assert r.status_code == 503
    assert r.json() == {"detail": "Volvemos pronto"}
    assert inferencia.peticiones == []


def test_inferencia_sin_configurar_503(client, inferencia, monkeypatch):
    monkeypatch.setattr(inference, "configured", lambda: False)
    r = client.post(URL, files=jpeg(), headers=auth())
    assert r.status_code == 503


def test_limite_por_usuario_429(client, inferencia):
    for _ in range(3):  # PREDICT_RATE_LIMIT=3 en conftest
        assert client.post(URL, files=jpeg(), headers=auth()).status_code == 200
    r = client.post(URL, files=jpeg(), headers=auth())
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0
    assert len(inferencia.predicciones) == 3
    # Otro usuario no se ve afectado
    assert client.post(URL, files=jpeg(), headers=auth(f"{TOKEN_OK}-otro")).status_code == 200


# ---- validación de la imagen ---- #

@pytest.mark.parametrize("archivo, codigo", [
    (("a.gif", b"GIF89a", "image/gif"), 415),
    (("a.txt", b"hola", "text/plain"), 415),
    (("a.jpg", b"", "image/jpeg"), 400),
    (("a.jpg", b"0" * (1024 * 1024 + 1), "image/jpeg"), 413),  # PREDICT_MAX_UPLOAD_MB=1
])
def test_imagen_no_valida(client, inferencia, archivo, codigo):
    r = client.post(URL, files={"file": archivo}, headers=auth())
    assert r.status_code == codigo
    assert inferencia.peticiones == []


def test_imagen_no_valida_no_gasta_el_limite(client, inferencia):
    for _ in range(5):
        client.post(URL, files={"file": ("a.gif", b"GIF", "image/gif")}, headers=auth())
    assert client.post(URL, files=jpeg(), headers=auth()).status_code == 200


def test_sin_archivo_422(client, inferencia):
    assert client.post(URL, headers=auth()).status_code == 422


# ---- errores de la inferencia ---- #

@pytest.mark.parametrize("respuesta, codigo", [
    (httpx.ReadTimeout("lento"), 504),
    (httpx.ConnectError("caído"), 502),
    (httpx.Response(500, json={"detail": "boom"}), 502),
    (httpx.Response(403, text="Forbidden"), 502),   # permisos del backend (run.invoker)
    (httpx.Response(401, text="Unauthorized"), 502),
    (httpx.Response(422, json={"detail": "Imagen ilegible"}), 422),
    (httpx.Response(200, text="no es json"), 502),
])
def test_errores_de_la_inferencia(client, inferencia, respuesta, codigo):
    inferencia.respuesta = respuesta
    r = client.post(URL, files=jpeg(), headers=auth())
    assert r.status_code == codigo
    assert r.json()["detail"]


def test_error_4xx_de_la_inferencia_conserva_su_detalle(client, inferencia):
    inferencia.respuesta = httpx.Response(422, json={"detail": "Imagen ilegible"})
    r = client.post(URL, files=jpeg(), headers=auth())
    assert r.json() == {"detail": "Imagen ilegible"}
