"""Cadena de timeouts app > backend > inferencia (services/inference.timeout_predict)."""

import pytest

from app.core.config import get_settings
from app.flags.catalog import AppConfig
from app.services import inference


@pytest.mark.parametrize("request_timeout_s, esperado", [
    (60, 57),    # valor por defecto de la flag
    (30, 27),
    (8, 5),
    (5, 2),      # mínimo de la flag: nunca menos de TIMEOUT_MIN_S
    (120, 115),  # máximo de la flag: tope INFERENCE_TIMEOUT_S (conftest: 115)
])
def test_timeout_predict(request_timeout_s, esperado):
    assert inference.timeout_predict(request_timeout_s) == esperado


def test_el_backend_siempre_responde_antes_que_la_app():
    """Para todo request_timeout_s válido, el backend espera menos que la app y menos que
    los 120 s con los que Cloud Run corta la inferencia."""
    campo = AppConfig.model_fields["request_timeout_s"]
    minimo = next(m.ge for m in campo.metadata if hasattr(m, "ge"))
    maximo = next(m.le for m in campo.metadata if hasattr(m, "le"))
    for t in range(minimo, maximo + 1):
        espera = inference.timeout_predict(t)
        assert espera < t
        assert espera < 120


def test_tope_por_defecto_por_debajo_de_la_inferencia():
    assert get_settings().inference_timeout_s < 120
