"""Administradores del panel (colección `admins`, id = nombre de usuario en minúsculas).

Documento: {username, password_hash, role, active, created_at, last_login_at}.
El primer admin se crea con `scripts/create_admin.py`; no hay registro abierto.
"""

import re
from dataclasses import dataclass
from typing import Optional

from ..core.security import hash_password
from .firestore import ADMINS, utcnow
from .replication import get_replicator, op_create, op_update

ROLES = ("lector", "editor", "superadmin")
ROLE_LEVEL = {r: i for i, r in enumerate(ROLES)}
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,40}$")


@dataclass
class Admin:
    username: str
    role: str
    active: bool
    password_hash: str

    def can(self, min_role: str) -> bool:
        return ROLE_LEVEL.get(self.role, -1) >= ROLE_LEVEL[min_role]


def normalize(username: str) -> str:
    return (username or "").strip().lower()


def get(username: str) -> Optional[Admin]:
    username = normalize(username)
    if not USERNAME_RE.match(username):
        return None
    doc = get_replicator().read(lambda db: db.collection(ADMINS).document(username).get())
    if not doc.exists:
        return None
    d = doc.to_dict()
    return Admin(username=username, role=d.get("role", "lector"),
                 active=bool(d.get("active", False)), password_hash=d.get("password_hash", ""))


def create(username: str, password: str, role: str) -> None:
    username = normalize(username)
    if not USERNAME_RE.match(username):
        raise ValueError("usuario inválido: 3–40 caracteres a-z 0-9 . _ -")
    if role not in ROLES:
        raise ValueError(f"rol inválido; usa uno de {ROLES}")
    get_replicator().write(op_create(f"{ADMINS}/{username}", {
        "username": username,
        "password_hash": hash_password(password),
        "role": role,
        "active": True,
        "created_at": utcnow(),
        "last_login_at": None,
    }))


def set_password(username: str, password: str) -> None:
    get_replicator().write(op_update(f"{ADMINS}/{normalize(username)}",
                                     {"password_hash": hash_password(password)}))


def update_password_hash(username: str, password_hash: str) -> None:
    get_replicator().write(op_update(f"{ADMINS}/{normalize(username)}", {"password_hash": password_hash}))


def record_login(username: str) -> None:
    get_replicator().write(op_update(f"{ADMINS}/{normalize(username)}", {"last_login_at": utcnow()}))
