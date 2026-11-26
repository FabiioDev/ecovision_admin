"""Cliente de Firestore compartido y nombres de colecciones."""

import threading
from datetime import datetime, timezone

from google.cloud import firestore

from ..core.config import get_settings

ADMINS = "admins"
APP_CONFIG = "app_config"
APP_CONFIG_CURRENT = "current"
APP_CONFIG_VERSIONS = "versions"
USERS = "users"
AUDIT_LOG = "audit_log"

_client: firestore.Client | None = None
_lock = threading.Lock()


def get_db() -> firestore.Client:
    """Client of the PRIMARY database only. Services use `replication` instead,
    which writes to both databases and falls back on reads."""
    global _client
    with _lock:
        if _client is None:
            _client = firestore.Client(project=get_settings().google_cloud_project)
        return _client


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
