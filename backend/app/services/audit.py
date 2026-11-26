"""Registro de auditoría: toda escritura del panel deja una entrada en `audit_log`.

Documento: {ts, actor, action, target, details, ip}. Nunca se guardan contraseñas.
"""

import logging
from typing import Any, Optional

from google.cloud import firestore

from .firestore import AUDIT_LOG, utcnow
from .replication import get_replicator, op_set

log = logging.getLogger("ecovision.audit")

ACTION_LABELS = {
    "admin.login": "Inicio de sesión",
    "admin.login_failed": "Login fallido",
    "admin.logout": "Cierre de sesión",
    "admin.create": "Administrador creado",
    "admin.password": "Contraseña de admin cambiada",
    "config.update": "Config actualizada",
    "config.reset": "Config restablecida a valores por defecto",
    "config.rollback": "Config restaurada desde un cambio anterior",
    "user.create": "Usuario creado",
    "user.update": "Usuario editado",
    "user.password": "Contraseña de usuario cambiada",
    "user.disable": "Usuario desactivado",
    "user.enable": "Usuario activado",
    "user.delete": "Usuario eliminado",
}


def record(actor: str, action: str, target: str = "", details: Optional[dict[str, Any]] = None,
           ip: str = "") -> None:
    """Escribe una entrada. Un fallo de auditoría se registra en logs pero no
    interrumpe la operación que ya se realizó."""
    entry = {
        "ts": utcnow(),
        "actor": actor,
        "action": action,
        "target": target,
        "details": details or {},
        "ip": ip,
    }
    try:
        rep = get_replicator()
        # same generated id in both databases
        doc_id = rep.nodes[0].client.collection(AUDIT_LOG).document().id
        rep.write(op_set(f"{AUDIT_LOG}/{doc_id}", entry))
    except Exception:  # noqa: BLE001
        log.exception("no se pudo escribir la auditoría: %s", {k: entry[k] for k in ("actor", "action", "target")})


def list_entries(limit: int = 50, cursor: Optional[str] = None) -> tuple[list[dict], Optional[str]]:
    """Entradas más recientes primero. `cursor` es el id del último documento de
    la página anterior. Devuelve (entradas, cursor siguiente o None)."""
    def _page(db):
        coll = db.collection(AUDIT_LOG)
        query = coll.order_by("ts", direction=firestore.Query.DESCENDING).limit(limit + 1)
        if cursor:
            last = coll.document(cursor).get()
            if last.exists:
                query = query.start_after(last)
        return list(query.stream())

    docs = get_replicator().read(_page)
    entries = []
    for d in docs[:limit]:
        e = d.to_dict()
        e["id"] = d.id
        e["action_label"] = ACTION_LABELS.get(e.get("action", ""), e.get("action", ""))
        entries.append(e)
    next_cursor = docs[limit - 1].id if len(docs) > limit else None
    return entries, next_cursor
