"""POST /demo/predict: flags impuestas en el servidor, validación compartida y timeout."""

from conftest import PREDICCION, jpeg, usar_config

URL = "/demo/predict"


def test_demo_apagada_503(client, inferencia, monkeypatch):
    usar_config(monkeypatch, demo_enabled=False)
    assert client.post(URL, files=jpeg()).status_code == 503
    assert inferencia.peticiones == []


def test_demo_en_mantenimiento_503(client, inferencia, monkeypatch):
    usar_config(monkeypatch, demo_enabled=True, maintenance_mode=True, maintenance_message="Pronto")
    r = client.post(URL, files=jpeg())
    assert r.status_code == 503
    assert r.json()["detail"] == "Pronto"


def test_demo_activa_usa_el_timeout_de_la_config(client, inferencia, monkeypatch):
    usar_config(monkeypatch, demo_enabled=True, request_timeout_s=40)
    r = client.post(URL, files=jpeg())
    assert r.status_code == 200
    assert r.json() == PREDICCION
    assert inferencia.timeouts[-1] == 37
    assert inferencia.predicciones[0].url.params["umbral"] == "0.3"


def test_demo_valida_la_imagen(client, inferencia, monkeypatch):
    usar_config(monkeypatch, demo_enabled=True)
    r = client.post(URL, files={"file": ("a.gif", b"GIF", "image/gif")})
    assert r.status_code == 415
    assert inferencia.peticiones == []
