"""Panel de administración (/admin): login, config de la app, usuarios y auditoría.

Roles:
  lector      — solo consulta
  editor      — edita la config y gestiona usuarios
  superadmin  — además elimina usuarios
Toda escritura queda registrada en `audit_log`.
"""

import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..core.config import get_settings
from ..core.rate_limit import SlidingWindowLimiter
from ..core.security import hash_password, needs_rehash, verify_password
from ..flags.catalog import DEFAULT_CONFIG, FLAG_SPECS, GROUPS, diff, parse_form, to_form_values
from ..services import admins, audit, config_store, users
from . import session as sess
from .session import CurrentAdmin, current_admin, require

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/admin", include_in_schema=False)

_settings = get_settings()
_login_by_user = SlidingWindowLimiter(_settings.login_max_attempts, _settings.login_window_s)
_login_by_ip = SlidingWindowLimiter(_settings.login_max_attempts * 4, _settings.login_window_s)


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def _fmt_dt(value: Any) -> str:
    if not value:
        return "—"
    try:
        return value.astimezone().strftime("%Y-%m-%d %H:%M")
    except (AttributeError, ValueError):
        return str(value)


templates.env.filters["dt"] = _fmt_dt
templates.env.filters["tojson_pretty"] = lambda v: json.dumps(v, ensure_ascii=False, indent=2, default=str)


def render(request: Request, name: str, ctx: Optional[dict] = None,
           admin: Optional[CurrentAdmin] = None, status_code: int = 200) -> HTMLResponse:
    flash = sess.read_flash(request)
    context = {"admin": admin, "flash": flash, **(ctx or {})}
    resp = templates.TemplateResponse(request, name, context, status_code=status_code)
    if flash:
        sess.clear_flash(resp)
    return resp


def redirect(url: str, message: Optional[str] = None, kind: str = "ok") -> RedirectResponse:
    resp = RedirectResponse(url, status_code=303)
    if message:
        sess.flash(resp, message, kind)
    return resp


# --------------------------------------------------------------------------- #
# Login / logout
# --------------------------------------------------------------------------- #
@router.get("")
@router.get("/")
def index():
    return RedirectResponse("/admin/config", status_code=303)


@router.get("/login")
def login_form(request: Request):
    token = sess.login_csrf_token(request)
    resp = render(request, "login.html", {"csrf": token, "error": None})
    sess.set_login_csrf(resp, token)
    return resp


@router.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form(""),
          csrf_token: str = Form("")):
    sess.check_login_csrf(request, csrf_token)
    ip = sess.client_ip(request)
    user_key = admins.normalize(username)
    token = sess.login_csrf_token(request)

    def fail(msg: str, code: int = 401):
        return render(request, "login.html", {"csrf": token, "error": msg, "username": username},
                      status_code=code)

    wait = max(_login_by_user.blocked_for(f"u:{user_key}"), _login_by_ip.blocked_for(f"ip:{ip}"))
    if wait:
        return fail(f"Demasiados intentos. Espera {wait // 60 + 1} min.", 429)

    admin = admins.get(user_key)
    ok = verify_password(admin.password_hash if admin else None, password)
    if not ok or admin is None or not admin.active:
        _login_by_user.hit(f"u:{user_key}")
        _login_by_ip.hit(f"ip:{ip}")
        audit.record((user_key or "?")[:60], "admin.login_failed", ip=ip)
        return fail("Usuario o contraseña incorrectos.")

    _login_by_user.reset(f"u:{user_key}")
    if needs_rehash(admin.password_hash):
        admins.update_password_hash(admin.username, hash_password(password))
    admins.record_login(admin.username)
    audit.record(admin.username, "admin.login", ip=ip)

    resp = RedirectResponse("/admin/config", status_code=303)
    sess.start_session(resp, admin.username)
    resp.delete_cookie(sess.LOGIN_CSRF_COOKIE, path=sess.COOKIE_PATH)
    return resp


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(""),
           admin: CurrentAdmin = Depends(current_admin)):
    sess.check_csrf(admin, csrf_token)
    audit.record(admin.username, "admin.logout", ip=sess.client_ip(request))
    resp = RedirectResponse("/admin/login", status_code=303)
    sess.end_session(resp)
    return resp


# --------------------------------------------------------------------------- #
# Config de la app (flags y parámetros)
# --------------------------------------------------------------------------- #
def _config_page(request: Request, admin: CurrentAdmin, values: dict, errors: dict,
                 status_code: int = 200) -> HTMLResponse:
    snap = config_store.get_current()
    return render(request, "config.html", {
        "groups": GROUPS,
        "specs": FLAG_SPECS,
        "values": values,
        "errors": errors,
        "defaults": to_form_values(DEFAULT_CONFIG),
        "snapshot": snap,
        "versions": config_store.list_versions(20),
    }, admin=admin, status_code=status_code)


@router.get("/config")
def config_view(request: Request, admin: CurrentAdmin = Depends(current_admin)):
    snap = config_store.get_current()
    return _config_page(request, admin, to_form_values(snap.config), {})


@router.post("/config")
async def config_save(request: Request, admin: CurrentAdmin = Depends(require("editor"))):
    form = await request.form()
    sess.check_csrf(admin, form.get("csrf_token"))
    cfg, values, errors = parse_form(form)
    if cfg is None:
        return await run_in_threadpool(_config_page, request, admin, values, errors, 422)

    def _save():
        current = config_store.get_current()
        changes = diff(current.config, cfg)
        if not changes:
            return None, {}
        snap, _ = config_store.save(cfg, admin.username, "config.update",
                                    str(form.get("note", "")).strip())
        audit.record(admin.username, "config.update", f"version:{snap.version}",
                     {"changes": changes}, sess.client_ip(request))
        return snap, changes

    snap, changes = await run_in_threadpool(_save)
    if snap is None:
        return redirect("/admin/config", "No hay cambios que guardar.", "info")
    return redirect("/admin/config",
                    f"Config guardada: versión {snap.version} ({len(changes)} cambio(s)).")


@router.post("/config/reset")
def config_reset(request: Request, csrf_token: str = Form(""),
                 admin: CurrentAdmin = Depends(require("editor"))):
    sess.check_csrf(admin, csrf_token)
    snap, previous = config_store.reset_defaults(admin.username)
    audit.record(admin.username, "config.reset", f"version:{snap.version}",
                 {"changes": diff(previous, snap.config)}, sess.client_ip(request))
    return redirect("/admin/config",
                    f"Valores por defecto restablecidos (versión {snap.version}).")


@router.get("/config/versions/{version}")
def config_version(version: int, request: Request, admin: CurrentAdmin = Depends(current_admin)):
    data = config_store.get_version(version)
    if data is None:
        return redirect("/admin/config", f"La versión {version} no existe.", "error")
    return render(request, "config_version.html", {"v": data}, admin=admin)


@router.post("/config/rollback/{version}")
def config_rollback(version: int, request: Request, csrf_token: str = Form(""),
                    admin: CurrentAdmin = Depends(require("editor"))):
    sess.check_csrf(admin, csrf_token)
    result = config_store.rollback(version, admin.username)
    if result is None:
        return redirect("/admin/config", f"La versión {version} no existe.", "error")
    snap, previous = result
    audit.record(admin.username, "config.rollback", f"version:{snap.version}",
                 {"from_version": version, "changes": diff(previous, snap.config)},
                 sess.client_ip(request))
    return redirect("/admin/config",
                    f"Restaurada la versión {version} como versión {snap.version}.")


# --------------------------------------------------------------------------- #
# Usuarios registrados (Firebase Auth)
# --------------------------------------------------------------------------- #
def _check_password(password: str) -> Optional[str]:
    if len(password) < 6:
        return "La contraseña debe tener al menos 6 caracteres."
    return None


@router.get("/users")
def users_list(request: Request, q: str = "", page: str = "",
               admin: CurrentAdmin = Depends(current_admin)):
    error, next_page = None, None
    try:
        if q.strip():
            items = users.search(q)
        else:
            items, next_page = users.list_page(page or None)
    except users.UserError as exc:
        items, error = [], str(exc)
    return render(request, "users.html",
                  {"items": items, "q": q, "next_page": next_page, "is_first": not page, "error": error},
                  admin=admin)


@router.get("/users/new")
def user_new_form(request: Request, admin: CurrentAdmin = Depends(require("editor"))):
    return render(request, "user_new.html", {"values": {}, "error": None}, admin=admin)


@router.post("/users/new")
def user_create(request: Request, email: str = Form(""), display_name: str = Form(""),
                password: str = Form(""), csrf_token: str = Form(""),
                admin: CurrentAdmin = Depends(require("editor"))):
    sess.check_csrf(admin, csrf_token)
    values = {"email": email, "display_name": display_name}
    error = _check_password(password)
    if not error:
        try:
            user = users.create(email, password, display_name, admin.username)
        except users.UserError as exc:
            error = str(exc)
    if error:
        return render(request, "user_new.html", {"values": values, "error": error},
                      admin=admin, status_code=422)
    audit.record(admin.username, "user.create", f"user:{user.uid}", {"email": user.email},
                 sess.client_ip(request))
    return redirect(f"/admin/users/{user.uid}", "Usuario creado.")


@router.get("/users/{uid}")
def user_detail(uid: str, request: Request, admin: CurrentAdmin = Depends(current_admin)):
    try:
        user = users.get(uid)
    except users.UserError as exc:
        return redirect("/admin/users", str(exc), "error")
    if user is None:
        return redirect("/admin/users", "El usuario no existe.", "error")
    return render(request, "user_detail.html",
                  {"user": user, "profile": users.get_profile(uid), "min_pw": 6}, admin=admin)


@router.post("/users/{uid}")
def user_update(uid: str, request: Request, email: str = Form(""), display_name: str = Form(""),
                csrf_token: str = Form(""), admin: CurrentAdmin = Depends(require("editor"))):
    sess.check_csrf(admin, csrf_token)
    before = users.get(uid)
    if before is None:
        return redirect("/admin/users", "El usuario no existe.", "error")
    try:
        after = users.update(uid, email, display_name)
    except users.UserError as exc:
        return redirect(f"/admin/users/{uid}", str(exc), "error")
    changes = {k: [getattr(before, k), getattr(after, k)] for k in ("email", "display_name")
               if getattr(before, k) != getattr(after, k)}
    if changes:
        audit.record(admin.username, "user.update", f"user:{uid}", {"changes": changes},
                     sess.client_ip(request))
    return redirect(f"/admin/users/{uid}", "Datos guardados." if changes else "Sin cambios.",
                    "ok" if changes else "info")


@router.post("/users/{uid}/password")
def user_password(uid: str, request: Request, password: str = Form(""), csrf_token: str = Form(""),
                  admin: CurrentAdmin = Depends(require("editor"))):
    sess.check_csrf(admin, csrf_token)
    error = _check_password(password)
    if error:
        return redirect(f"/admin/users/{uid}", error, "error")
    try:
        users.set_password(uid, password)
    except users.UserError as exc:
        return redirect(f"/admin/users/{uid}", str(exc), "error")
    audit.record(admin.username, "user.password", f"user:{uid}", ip=sess.client_ip(request))
    return redirect(f"/admin/users/{uid}", "Contraseña cambiada y sesiones cerradas.")


@router.post("/users/{uid}/toggle")
def user_toggle(uid: str, request: Request, disable: str = Form(...), csrf_token: str = Form(""),
                admin: CurrentAdmin = Depends(require("editor"))):
    sess.check_csrf(admin, csrf_token)
    to_disable = disable == "1"
    try:
        users.set_disabled(uid, to_disable)
    except users.UserError as exc:
        return redirect(f"/admin/users/{uid}", str(exc), "error")
    audit.record(admin.username, "user.disable" if to_disable else "user.enable", f"user:{uid}",
                 ip=sess.client_ip(request))
    return redirect(f"/admin/users/{uid}",
                    "Usuario desactivado; sus sesiones se cerraron." if to_disable else "Usuario activado.")


@router.post("/users/{uid}/delete")
def user_delete(uid: str, request: Request, confirm_email: str = Form(""), csrf_token: str = Form(""),
                admin: CurrentAdmin = Depends(require("superadmin"))):
    sess.check_csrf(admin, csrf_token)
    user = users.get(uid)
    if user is None:
        return redirect("/admin/users", "El usuario no existe.", "error")
    expected = user.email or user.uid
    if confirm_email.strip().lower() != expected.lower():
        return redirect(f"/admin/users/{uid}",
                        "Para eliminar escribe exactamente el correo (o UID) del usuario.", "error")
    try:
        users.delete(uid)
    except users.UserError as exc:
        return redirect(f"/admin/users/{uid}", str(exc), "error")
    audit.record(admin.username, "user.delete", f"user:{uid}", {"email": user.email},
                 sess.client_ip(request))
    return redirect("/admin/users", f"Usuario {expected} eliminado.")


# --------------------------------------------------------------------------- #
# Auditoría
# --------------------------------------------------------------------------- #
@router.get("/audit")
def audit_view(request: Request, cursor: str = "", admin: CurrentAdmin = Depends(current_admin)):
    entries, next_cursor = audit.list_entries(50, cursor or None)
    return render(request, "audit.html",
                  {"entries": entries, "next_cursor": next_cursor, "is_first": not cursor},
                  admin=admin)

