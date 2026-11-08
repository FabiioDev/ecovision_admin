"""Persistencia versionada de la config de la app en Firestore.

  app_config/current                    {config, version, schema, updated_at, updated_by}
  app_config/current/versions/{000042}  {config, version, schema, created_at, created_by, action, note}

Cada escritura (guardar, restablecer, volver a una versión) crea una versión
nueva; nunca se reescribe el historial. Si `current` no existe se sirven los
valores por defecto como versión 0, sin escribir nada.
"""

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from google.cloud import firestore

from ..flags.catalog import CONFIG_SCHEMA_VERSION, DEFAULT_CONFIG, AppConfig
from .firestore import APP_CONFIG, APP_CONFIG_CURRENT, APP_CONFIG_VERSIONS, get_db, utcnow


@dataclass
class ConfigSnapshot:
    config: AppConfig
    version: int
    updated_at: Optional[datetime]
    updated_by: Optional[str]


def _current_ref():
    return get_db().collection(APP_CONFIG).document(APP_CONFIG_CURRENT)


def _versions_ref():
    return _current_ref().collection(APP_CONFIG_VERSIONS)


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
    doc = _current_ref().get()
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
    db = get_db()
    cur_ref = _current_ref()
    payload = new_config.model_dump(mode="json")

    @firestore.transactional
    def _apply(tx: firestore.Transaction):
        snap = cur_ref.get(transaction=tx)
        prev = _snapshot_from(snap.to_dict() if snap.exists else None)
        version = prev.version + 1
        now = utcnow()
        tx.set(cur_ref, {
            "config": payload,
            "version": version,
            "schema": CONFIG_SCHEMA_VERSION,
            "updated_at": now,
            "updated_by": actor,
        })
        tx.set(_versions_ref().document(f"{version:06d}"), {
            "config": payload,
            "version": version,
            "schema": CONFIG_SCHEMA_VERSION,
            "created_at": now,
            "created_by": actor,
            "action": action,
            "note": note[:200],
        })
        return ConfigSnapshot(new_config, version, now, actor), prev.config

    result = _apply(db.transaction())
    invalidate_cache()
    return result


def reset_defaults(actor: str) -> tuple[ConfigSnapshot, AppConfig]:
    return save(AppConfig(), actor, "config.reset", "Restablecer valores por defecto")


def get_version(version: int) -> Optional[dict]:
    doc = _versions_ref().document(f"{version:06d}").get()
    return doc.to_dict() if doc.exists else None


def rollback(version: int, actor: str) -> Optional[tuple[ConfigSnapshot, AppConfig]]:
    data = get_version(version)
    if data is None:
        return None
    cfg = AppConfig.from_stored(data.get("config"))
    return save(cfg, actor, "config.rollback", f"Restaurada desde la versión {version}")


def list_versions(limit: int = 20) -> list[dict]:
    query = _versions_ref().order_by("version", direction=firestore.Query.DESCENDING).limit(limit)
    return [d.to_dict() for d in query.stream()]
