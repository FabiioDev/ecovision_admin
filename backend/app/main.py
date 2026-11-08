"""Punto de entrada del backend EcoVision.

ROLE=all|api|admin decide qué routers se montan, para poder desplegar la misma
imagen como servicio de API y como servicio de panel por separado.

  local:     uvicorn app.main:app --reload --port 8081      (desde backend/)
  Cloud Run: ver Dockerfile
"""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .core.config import get_settings

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Cabeceras para el panel (HTML). La API devuelve JSON y no las necesita todas.
_ADMIN_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    app = FastAPI(
        title="EcoVision Backend",
        version=__version__,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.url.path.startswith("/admin"):
            for k, v in _ADMIN_HEADERS.items():
                response.headers.setdefault(k, v)
        if settings.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    if settings.api_enabled:
        from .api.routes import router as api_router
        app.include_router(api_router)

    if settings.admin_enabled:
        if len(settings.secret_key) < 32:
            raise RuntimeError("SECRET_KEY debe tener al menos 32 caracteres para montar el panel")
        _mount_admin(app)

    @app.get("/", include_in_schema=False)
    def root():
        if settings.admin_enabled:
            return RedirectResponse("/admin/config", status_code=303)
        return {"service": "ecovision-backend", "version": __version__}

    return app


def _mount_admin(app: FastAPI) -> None:
    from .admin import session as sess
    from .admin.routes import render, router as admin_router

    app.mount("/admin/static", StaticFiles(directory=str(STATIC_DIR)), name="admin_static")
    app.include_router(admin_router)

    @app.exception_handler(sess.AdminAuthRequired)
    async def _auth_required(request: Request, exc: sess.AdminAuthRequired):
        resp = RedirectResponse("/admin/login", status_code=303)
        sess.end_session(resp)
        return resp

    @app.exception_handler(sess.AdminForbidden)
    async def _forbidden(request: Request, exc: sess.AdminForbidden):
        return render(request, "error.html", {"title": "Acceso denegado", "message": exc.message},
                      status_code=403)

    @app.exception_handler(sess.CsrfError)
    async def _csrf(request: Request, exc: sess.CsrfError):
        return render(request, "error.html",
                      {"title": "Formulario caducado",
                       "message": "El formulario expiró o no es válido. Vuelve atrás, recarga la página e inténtalo de nuevo."},
                      status_code=400)


app = create_app()
