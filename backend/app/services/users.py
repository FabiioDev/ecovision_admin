"""Usuarios registrados de la app: Firebase Authentication + perfil en Firestore (`users/{uid}`).

Firebase Auth es la fuente de verdad de la cuenta (correo, nombre, activo).
El documento `users/{uid}` guarda datos propios (fecha de alta, origen y, en la
Fase 5, cuota de imágenes).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from firebase_admin import auth

from ..core.firebase import get_firebase_app
from .firestore import USERS, get_db, utcnow

PAGE_SIZE = 50

PROVIDER_LABELS = {"password": "Correo", "google.com": "Google"}


class UserError(Exception):
    """Error mostrado al administrador en el panel."""


@dataclass
class AppUser:
    uid: str
    email: Optional[str]
    display_name: Optional[str]
    disabled: bool
    email_verified: bool
    providers: list[str]
    created_at: Optional[datetime]
    last_sign_in: Optional[datetime]


def _ms(ts: Optional[int]) -> Optional[datetime]:
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else None


def _to_user(r) -> AppUser:
    return AppUser(
        uid=r.uid,
        email=r.email,
        display_name=r.display_name,
        disabled=r.disabled,
        email_verified=r.email_verified,
        providers=[PROVIDER_LABELS.get(p.provider_id, p.provider_id) for p in (r.provider_data or [])],
        created_at=_ms(r.user_metadata.creation_timestamp if r.user_metadata else None),
        last_sign_in=_ms(r.user_metadata.last_sign_in_timestamp if r.user_metadata else None),
    )


def _wrap(exc: Exception) -> UserError:
    if isinstance(exc, auth.EmailAlreadyExistsError):
        return UserError("Ya existe una cuenta con ese correo.")
    if isinstance(exc, auth.UserNotFoundError):
        return UserError("El usuario no existe.")
    if isinstance(exc, ValueError):
        return UserError(f"Datos inválidos: {exc}")
    return UserError(f"Error de Firebase: {exc}")


def list_page(page_token: Optional[str] = None) -> tuple[list[AppUser], Optional[str]]:
    try:
        page = auth.list_users(page_token=page_token or None, max_results=PAGE_SIZE,
                               app=get_firebase_app())
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    return [_to_user(u) for u in page.users], (page.next_page_token or None)


def search(query: str) -> list[AppUser]:
    """Búsqueda exacta por correo o UID (Firebase Auth no admite búsqueda parcial)."""
    query = query.strip()
    if not query:
        return []
    app = get_firebase_app()
    try:
        r = auth.get_user_by_email(query, app=app) if "@" in query else auth.get_user(query, app=app)
    except (auth.UserNotFoundError, ValueError):
        return []
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    return [_to_user(r)]


def get(uid: str) -> Optional[AppUser]:
    try:
        return _to_user(auth.get_user(uid, app=get_firebase_app()))
    except (auth.UserNotFoundError, ValueError):
        return None
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc


def get_profile(uid: str) -> dict:
    doc = get_db().collection(USERS).document(uid).get()
    return doc.to_dict() if doc.exists else {}


def create(email: str, password: str, display_name: str, actor: str) -> AppUser:
    try:
        r = auth.create_user(email=email.strip(), password=password,
                             display_name=display_name.strip() or None,
                             app=get_firebase_app())
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    get_db().collection(USERS).document(r.uid).set({
        "email": r.email,
        "display_name": r.display_name,
        "created_at": utcnow(),
        "created_by": f"admin:{actor}",
    }, merge=True)
    return _to_user(r)


def update(uid: str, email: str, display_name: str) -> AppUser:
    kwargs = {"display_name": display_name.strip() or auth.DELETE_ATTRIBUTE}
    if email.strip():
        kwargs["email"] = email.strip()
    try:
        r = auth.update_user(uid, app=get_firebase_app(), **kwargs)
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    get_db().collection(USERS).document(uid).set(
        {"email": r.email, "display_name": r.display_name, "updated_at": utcnow()}, merge=True)
    return _to_user(r)


def set_password(uid: str, password: str) -> None:
    app = get_firebase_app()
    try:
        auth.update_user(uid, password=password, app=app)
        auth.revoke_refresh_tokens(uid, app=app)  # cierra sus sesiones abiertas
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc


def set_disabled(uid: str, disabled: bool) -> None:
    app = get_firebase_app()
    try:
        auth.update_user(uid, disabled=disabled, app=app)
        if disabled:
            auth.revoke_refresh_tokens(uid, app=app)
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc


def delete(uid: str) -> None:
    try:
        auth.delete_user(uid, app=get_firebase_app())
    except auth.UserNotFoundError:
        pass  # ya no existe en Auth: se limpia igualmente el perfil
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    get_db().collection(USERS).document(uid).delete()
    # FUTURO (Fase 5): borrar también sus imágenes en Cloud Storage
    # (users/{uid}/...) y sus metadatos en la colección `images`.
