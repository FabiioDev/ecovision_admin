"""GET /api/v1/health y estado de la inferencia (services/inference.estado)."""

import asyncio

import httpx

from app.services import inference


def test_health_con_la_inferencia_lista(client, inferencia):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["service"] == "backend"
    assert body["inferencia"] == "lista"
    assert r.headers["cache-control"] == "no-store"


def test_lista_se_reutiliza_sin_volver_a_consultar(client, inferencia):
    client.get("/api/v1/health")
    client.get("/api/v1/health")
    assert len(inferencia.peticiones) == 1


def test_inferencia_con_error_no_disponible(client, inferencia):
    inferencia.salud = httpx.Response(503)
    assert client.get("/api/v1/health").json()["inferencia"] == "no_disponible"


def test_inferencia_caida_no_disponible(client, inferencia):
    inferencia.salud = httpx.ConnectError("caída")
    assert client.get("/api/v1/health").json()["inferencia"] == "no_disponible"


def test_no_disponible_se_vuelve_a_comprobar(client, inferencia):
    inferencia.salud = httpx.Response(503)
    client.get("/api/v1/health")
    inferencia.salud = httpx.Response(200, json={"clases": []})
    assert client.get("/api/v1/health").json()["inferencia"] == "lista"


def test_sin_inference_url(client, monkeypatch):
    monkeypatch.setattr(inference, "configured", lambda: False)
    assert client.get("/api/v1/health").json()["inferencia"] == "sin_configurar"


def test_arranque_en_frio_despertando_y_luego_lista(inferencia):
    """Si la inferencia tarda más que la espera, responde "despertando" sin bloquear y la
    comprobación sigue; al terminar, el estado pasa a "lista"."""
    async def lenta(request):
        await asyncio.sleep(0.3)
        return httpx.Response(200, json={"clases": []})

    inferencia.salud = lenta

    async def escenario():
        primero = await inference.estado(espera_s=0.05)
        # Una segunda llamada mientras tanto reutiliza la misma comprobación
        segundo = await inference.estado(espera_s=0.05)
        await asyncio.sleep(0.4)
        tercero = await inference.estado(espera_s=0.05)
        return primero, segundo, tercero

    assert asyncio.run(escenario()) == ("despertando", "despertando", "lista")
    assert len(inferencia.peticiones) == 1
