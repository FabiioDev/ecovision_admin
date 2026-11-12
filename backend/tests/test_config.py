"""Config de la app: validación del catálogo y GET /api/v1/config(/public)."""

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.flags.catalog import AppConfig
from conftest import TOKEN_DESACTIVADO, auth, usar_config


# ---- catálogo (mismo modelo al escribir desde el panel y al servir) ---- #

@pytest.mark.parametrize("campo, valor", [
    ("score_threshold", 0.2), ("score_threshold", 1.5), ("nms_iou", -0.1),
    ("max_detections", 2), ("request_timeout_s", 121), ("retries", 6),
    ("min_app_version", "uno"), ("announcement_1", "x" * 501),
])
def test_valores_fuera_de_limites_se_rechazan_al_escribir(campo, valor):
    with pytest.raises(ValidationError):
        AppConfig(**{campo: valor})


def test_claves_desconocidas_se_rechazan_al_escribir():
    with pytest.raises(ValidationError):
        AppConfig(clave_inventada=True)


def test_from_stored_tolera_claves_retiradas_ausentes_y_fuera_de_limites():
    cfg = AppConfig.from_stored({"guest_mode_enabled": True, "score_threshold": 0.2,
                                 "request_timeout_s": 500, "camera_enabled": False})
    assert cfg.score_threshold == 0.3
    assert cfg.request_timeout_s == 120
    assert cfg.camera_enabled is False
    assert cfg.gallery_enabled is True  # ausente → valor por defecto


def test_from_stored_vacio_son_los_valores_por_defecto():
    assert AppConfig.from_stored(None) == AppConfig()


# ---- GET /api/v1/config ---- #

def test_config_con_etag_y_304(client, monkeypatch):
    usar_config(monkeypatch, version=7, camera_enabled=False)
    r = client.get("/api/v1/config")
    assert r.status_code == 200
    assert r.headers["etag"] == '"v7"'
    body = r.json()
    assert body["config_version"] == 7
    assert body["config"]["camera_enabled"] is False
    assert client.get("/api/v1/config", headers={"If-None-Match": '"v7"'}).status_code == 304
    assert client.get("/api/v1/config", headers={"If-None-Match": '"v6"'}).status_code == 200


def test_config_con_firestore_caido_503(client, monkeypatch):
    from app.services import config_store

    def falla(ttl_s):
        raise RuntimeError("Firestore no responde")
    monkeypatch.setattr(config_store, "get_current_cached", falla)
    assert client.get("/api/v1/config").status_code == 503


def test_config_exige_token_con_config_require_auth(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "config_require_auth", True)
    assert client.get("/api/v1/config").status_code == 401
    assert client.get("/api/v1/config", headers=auth(TOKEN_DESACTIVADO)).status_code == 403
    assert client.get("/api/v1/config", headers=auth()).status_code == 200


def test_config_publica_sin_token(client, monkeypatch):
    usar_config(monkeypatch, version=3, maintenance_mode=True, maintenance_message="Pronto")
    monkeypatch.setattr(get_settings(), "config_require_auth", True)
    r = client.get("/api/v1/config/public")
    assert r.status_code == 200
    assert r.json() == {"config_version": 3, "maintenance_mode": True,
                        "maintenance_message": "Pronto", "min_app_version": "1.0"}
