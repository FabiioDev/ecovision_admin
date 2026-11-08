"""Sesión del panel por cookie firmada, CSRF, roles y mensajes flash.

Cookie `ev_admin`: HttpOnly, Secure (configurable para local), SameSite=Lax,
path=/admin. Contiene {u: usuario, c: token CSRF}. En cada petición se vuelve a
leer el admin en Firestore, así desactivarlo o cambiar su rol surte efecto de inmediato.
"""

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Request
from fastapi.responses import Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from ..core.config import get_settings
from ..core.security import new_token, tokens_equal
from ..services import admins

SESSION_COOKIE = "ev_admin"
LOGIN_CSRF_COOKIE = "ev_login_csrf"
FLASH_COOKIE = "ev_flash"
COOKIE_PATH = "/admin"


class AdminAuthRequired(Exception):
    """Sin sesión válida: se redirige a /admin/login."""


class AdminForbidden(Exception):
    def __init__(self, message: str = "No tienes permiso para esta acción."):
        self.message = message


class CsrfError(Exception):
    pass


@dataclass
class CurrentAdmin:
    username: str
    role: str
    csrf: str

    def can(self, min_role: str) -> bool:
        return admins.ROLE_LEVEL.get(self.role, -1) >= admins.ROLE_LEVEL[min_role]


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt=salt)


def _set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(name, value, max_age=max_age, path=COOKIE_PATH, httponly=True,
                        secure=get_settings().cookie_secure, samesite="lax")


# ---- sesión ---- #
def start_session(response: Response, username: str) -> None:
    s = get_settings()
    token = _serializer("session").dumps({"u": username, "c": new_token()})
    _set_cookie(response, SESSION_COOKIE, token, s.session_max_age_s)


def end_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path=COOKIE_PATH)


def _read_session(request: Request) -> Optional[dict]:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        return _serializer("session").loads(raw, max_age=get_settings().session_max_age_s)
    except BadSignature:
        return None


def current_admin(request: Request) -> CurrentAdmin:
    data = _read_session(request)
    if not data:
        raise AdminAuthRequired()
    admin = admins.get(data.get("u", ""))
    if admin is None or not admin.active:
        raise AdminAuthRequired()
    return CurrentAdmin(username=admin.username, role=admin.role, csrf=data.get("c", ""))


def require(min_role: str):
    def _dep(admin: CurrentAdmin = Depends(current_admin)) -> CurrentAdmin:
        if not admin.can(min_role):
            raise AdminForbidden(f"Esta acción requiere el rol «{min_role}».")
        return admin
    return _dep


def check_csrf(admin: CurrentAdmin, token: Optional[str]) -> None:
    if not tokens_equal(admin.csrf, token):
        raise CsrfError()


# ---- CSRF del formulario de login (doble envío con cookie) ---- #
def login_csrf_token(request: Request) -> str:
    return request.cookies.get(LOGIN_CSRF_COOKIE) or new_token()


def set_login_csrf(response: Response, token: str) -> None:
    _set_cookie(response, LOGIN_CSRF_COOKIE, token, 3600)


def check_login_csrf(request: Request, token: Optional[str]) -> None:
    if not tokens_equal(request.cookies.get(LOGIN_CSRF_COOKIE), token):
        raise CsrfError()


# ---- mensajes flash ---- #
def flash(response: Response, message: str, kind: str = "ok") -> None:
    _set_cookie(response, FLASH_COOKIE, _serializer("flash").dumps({"m": message, "k": kind}), 120)


def read_flash(request: Request) -> Optional[dict]:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return None
    try:
        return _serializer("flash").loads(raw, max_age=120)
    except BadSignature:
        return None


def clear_flash(response: Response) -> None:
    response.delete_cookie(FLASH_COOKIE, path=COOKIE_PATH)


def client_ip(request: Request) -> str:
    # uvicorn --proxy-headers ya resuelve X-Forwarded-For en request.client
    return request.client.host if request.client else ""
