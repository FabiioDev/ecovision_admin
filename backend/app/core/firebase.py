"""Inicialización de Firebase Admin y verificación de ID tokens de la app.

Usa Application Default Credentials: en Cloud Run, la cuenta de servicio del
servicio; en local, `gcloud auth application-default login`.
"""

import threading
from typing import Optional

import firebase_admin
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth

from .config import get_settings

_lock = threading.Lock()


def get_firebase_app() -> firebase_admin.App:
    with _lock:
        try:
            return firebase_admin.get_app()
        except ValueError:
            project = get_settings().google_cloud_project
            options = {"projectId": project} if project else None
            return firebase_admin.initialize_app(options=options)


bearer_scheme = HTTPBearer(auto_error=False)


def verify_app_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> dict:
    """Dependencia para rutas /api/v1 protegidas: valida el ID token de Firebase.

    Rechaza tokens revocados y usuarios desactivados desde el panel.
    """
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "falta el token",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        return auth.verify_id_token(creds.credentials, app=get_firebase_app(), check_revoked=True)
    except auth.UserDisabledError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "usuario desactivado")
    except (auth.RevokedIdTokenError, auth.ExpiredIdTokenError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token expirado o revocado",
                            headers={"WWW-Authenticate": "Bearer"})
    except (auth.InvalidIdTokenError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token inválido",
                            headers={"WWW-Authenticate": "Bearer"})
