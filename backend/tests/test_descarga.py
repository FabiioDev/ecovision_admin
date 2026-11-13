"""Descarga pública del APK: /descargar y /descargar/apk."""

import pytest
from pydantic import ValidationError

from app.flags.catalog import AppConfig, parse_form
from conftest import usar_config

APK = "https://github.com/FabiioDev/EcoVision_SV/releases/download/v1.0.0/EcoVisionSV-1.0.0.apk"
SHA = "a" * 64


def publicar(monkeypatch, **extra):
    valores = dict(download_enabled=True, apk_version="1.0.0", apk_url=APK, apk_sha256=SHA)
    valores.update(extra)
    usar_config(monkeypatch, **valores)


# ---- página ---- #

def test_apagada_por_defecto(client):
    r = client.get("/descargar")
    assert r.status_code == 503
    assert "no está disponible" in r.text
    assert "/descargar/apk" not in r.text


def test_activa_sin_enlace_no_disponible(client, monkeypatch):
    usar_config(monkeypatch, download_enabled=True)
    assert client.get("/descargar").status_code == 503


def test_pagina_con_version_sha_y_boton(client, monkeypatch):
    publicar(monkeypatch)
    r = client.get("/descargar")
    assert r.status_code == 200
    assert "Versión 1.0.0" in r.text and SHA in r.text
    assert 'href="/descargar/apk"' in r.text
    assert "/admin" not in r.text  # aislada del panel


def test_cabeceras_de_seguridad(client, monkeypatch):
    publicar(monkeypatch)
    r = client.get("/descargar")
    assert "script-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_en_mantenimiento_se_puede_descargar(client, monkeypatch):
    publicar(monkeypatch, maintenance_mode=True, maintenance_message="Volvemos pronto.")
    r = client.get("/descargar")
    assert r.status_code == 200
    assert "Volvemos pronto." in r.text
    assert client.get("/descargar/apk", follow_redirects=False).status_code == 302


# ---- redirección ---- #

def test_redirige_al_release(client, monkeypatch):
    publicar(monkeypatch)
    r = client.get("/descargar/apk", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == APK
    assert r.headers["cache-control"] == "no-store"


def test_apagada_no_redirige_a_github(client, monkeypatch):
    publicar(monkeypatch, download_enabled=False)
    r = client.get("/descargar/apk", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/descargar"


def test_limite_por_ip(client, monkeypatch):
    publicar(monkeypatch)
    for _ in range(2):  # DOWNLOAD_RATE_LIMIT=2 en conftest
        assert client.get("/descargar/apk", follow_redirects=False).status_code == 302
    r = client.get("/descargar/apk", follow_redirects=False)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0
    assert "Espera" in r.text


# ---- catálogo: solo enlaces de GitHub Releases ---- #

@pytest.mark.parametrize("url", [
    "https://evil.example/EcoVisionSV.apk",
    "http://github.com/u/r/releases/download/v1/a.apk",
    "https://github.com.evil.example/u/r/releases/download/v1/a.apk",
    "https://github.com/u/r/releases/download/v1/a.apk?x=1",
    "https://github.com/u/r/blob/main/a.apk",
    "https://github.com/u/r/releases/download/v1/a.zip",
    "javascript:alert(1)",
])
def test_apk_url_rechaza_otros_sitios(url):
    with pytest.raises(ValidationError):
        AppConfig(apk_url=url)


@pytest.mark.parametrize("campo, valor", [
    ("apk_sha256", "ABC"), ("apk_sha256", "A" * 64), ("apk_version", "v1"),
])
def test_version_y_sha_validados(campo, valor):
    with pytest.raises(ValidationError):
        AppConfig(**{campo: valor})


def test_panel_muestra_errores_en_espanol():
    form = {k: "" for k in AppConfig.model_fields}
    form.update(score_threshold="0.3", nms_iou="0.3", max_detections="100",
                request_timeout_s="60", retries="0", min_app_version="1.0",
                maintenance_message="m", apk_url="https://evil.example/a.apk", apk_sha256="xyz")
    for k, v in AppConfig().model_dump().items():
        if isinstance(v, bool):
            form.pop(k, None)
    cfg, _, errores = parse_form(form)
    assert cfg is None
    assert "GitHub Releases" in errores["apk_url"]
    assert "hexadecimales" in errores["apk_sha256"]
