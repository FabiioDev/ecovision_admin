"""Persistencia versionada de la config de la app en Firestore.

  app_config/current                    {config, version, schema, updated_at, updated_by}
  app_config/current/versions/{000042}  {config, version, schema, created_at, created_by, action, note}

Cada escritura (guardar, restablecer, restaurar) crea una entrada nueva en el
historial; nunca se reescribe. El número de "versión" es solo un contador de cambios
de la config (auditoría y ETag), no tiene relación con la versión de la app.
Si `current` no existe se sirven los valores por defecto como versión 0, sin escribir nada.
"""

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from google.cloud import firestore

from ..flags.catalog import CONFIG_SCHEMA_VERSION, DEFAULT_CONFIG, AppConfig
from .firestore import APP_CONFIG, APP_CONFIG_CURRENT, APP_CONFIG_VERSIONS, utcnow
from .replication import get_replicator, op_set

CURRENT_PATH = f"{APP_CONFIG}/{APP_CONFIG_CURRENT}"


@dataclass
class ConfigSnapshot:
    config: AppConfig
    version: int
    updated_at: Optional[datetime]
    updated_by: Optional[str]


def _current_ref(db):
    return db.collection(APP_CONFIG).document(APP_CONFIG_CURRENT)


def _versions_ref(db):
    return _current_ref(db).collection(APP_CONFIG_VERSIONS)


def _snapshot_from(data: Optional[dict]) -> ConfigSnapshot:
    if not data:
        return ConfigSnapshot(DEFAULT_CONFIG, 0, None, None)
    return ConfigSnapshot(
        config=AppConfig.from_stored(data.get("config")),
        version=int(data.get("version", 0)),
        updated_at=data.get("updated_at"),
        updated_by=data.get("updated_by"),
    )


def get_current() -> ConfigSnapshot:
    doc = get_replicator().read(lambda db: _current_ref(db).get())
    return _snapshot_from(doc.to_dict() if doc.exists else None)


# ---- caché en memoria para la API (muchas lecturas, pocas escrituras) ---- #
_cache: Optional[ConfigSnapshot] = None
_cache_at = 0.0
_cache_lock = threading.Lock()


def get_current_cached(ttl_s: int) -> ConfigSnapshot:
    """Config para la API. Si Firestore falla y hay una copia previa, la sirve
    aunque esté vencida; si no hay copia, propaga el error (la API responde 503
    y la app usa su caché o sus valores por defecto)."""
    global _cache, _cache_at
    with _cache_lock:
        if _cache is not None and time.monotonic() - _cache_at < ttl_s:
            return _cache
    try:
        snap = get_current()
    except Exception:
        with _cache_lock:
            if _cache is not None:
                return _cache
        raise
    with _cache_lock:
        _cache, _cache_at = snap, time.monotonic()
    return snap


def invalidate_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


# ---- escrituras ---- #
def save(new_config: AppConfig, actor: str, action: str, note: str = "") -> tuple[ConfigSnapshot, AppConfig]:
    """Guarda una versión nueva en una transacción. Devuelve (nuevo, config anterior)."""
    payload = new_config.model_dump(mode="json")

    def _apply(db, tx: firestore.Transaction):
        # The version number is decided in ONE database (inside its transaction)
        # and the same two documents are then copied to the other one.
        snap = _current_ref(db).get(transaction=tx)
        prev = _snapshot_from(snap.to_dict() if snap.exists else None)
        version = prev.version + 1
        now = utcnow()
        ops = [
            op_set(CURRENT_PATH, {
                "config": payload,
                "version": version,
                "schema": CONFIG_SCHEMA_VERSION,
                "updated_at": now,
                "updated_by": actor,
            }),
            op_set(f"{CURRENT_PATH}/{APP_CONFIG_VERSIONS}/{version:06d}", {
                "config": payload,
                "version": version,
                "schema": CONFIG_SCHEMA_VERSION,
                "created_at": now,
                "created_by": actor,
                "action": action,
                "note": note[:200],
            }),
        ]
        return (ConfigSnapshot(new_config, version, now, actor), prev.config), ops

    result = get_replicator().transaction(_apply)
    invalidate_cache()
    return result


def reset_defaults(actor: str) -> tuple[ConfigSnapshot, AppConfig]:
    return save(AppConfig(), actor, "config.reset", "Restablecer valores por defecto")


def get_version(version: int) -> Optional[dict]:
    doc = get_replicator().read(lambda db: _versions_ref(db).document(f"{version:06d}").get())
    return doc.to_dict() if doc.exists else None


def rollback(version: int, actor: str) -> Optional[tuple[ConfigSnapshot, AppConfig]]:
    data = get_version(version)
    if data is None:
        return None
    cfg = AppConfig.from_stored(data.get("config"))
    return save(cfg, actor, "config.rollback", f"Restaurada desde el cambio #{version}")


def list_versions(limit: int = 20) -> list[dict]:
    def _list(db):
        query = _versions_ref(db).order_by("version", direction=firestore.Query.DESCENDING).limit(limit)
        return [d.to_dict() for d in query.stream()]

    return get_replicator().read(_list)
