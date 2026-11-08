"""Hash de contraseñas de administradores (argon2id) y utilidades de seguridad."""

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_ph = PasswordHasher()

# Hash señuelo: se verifica contra él cuando el usuario no existe, para que el
# tiempo de respuesta no revele qué nombres de usuario son válidos.
_DUMMY_HASH = _ph.hash(secrets.token_urlsafe(16))

MIN_PASSWORD_LEN = 12


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _ph.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _ph.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def tokens_equal(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return secrets.compare_digest(a, b)
