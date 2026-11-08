# Imagen del backend EcoVision (API /api/v1 + panel /admin).
# No contiene secretos: SECRET_KEY y demás valores sensibles se inyectan en
# Cloud Run desde Secret Manager (--set-secrets).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    ROLE=all

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/

# Usuario sin privilegios
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8080

# Cloud Run termina TLS delante del contenedor: se confía en X-Forwarded-*
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} \
    --proxy-headers --forwarded-allow-ips="*" --no-server-header
