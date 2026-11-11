"""Crea un administrador del panel en Firestore (colección `admins`).

Es la ÚNICA forma de crear admins: el panel no tiene registro abierto.
La contraseña se pide por consola (no se pasa como argumento ni queda en el historial).

Uso (desde la raíz de ecovision_admin, con credenciales ADC configuradas):
    python scripts/create_admin.py --username fabio --role superadmin
    python scripts/create_admin.py --username fabio --reset-password
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

# Permite importar el paquete `app` de backend/ sin instalarlo y leer backend/.env
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

from app.core.security import MIN_PASSWORD_LEN  # noqa: E402
from app.services import admins, audit  # noqa: E402


def ask_password() -> str:
    while True:
        pw = getpass.getpass(f"Contraseña (mín. {MIN_PASSWORD_LEN} caracteres): ")
        if len(pw) < MIN_PASSWORD_LEN:
            print("Demasiado corta.")
            continue
        if pw != getpass.getpass("Repite la contraseña: "):
            print("No coinciden.")
            continue
        return pw


def main() -> int:
    parser = argparse.ArgumentParser(description="Crea un administrador del panel EcoVision")
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", choices=admins.ROLES, default="superadmin")
    parser.add_argument("--reset-password", action="store_true",
                        help="cambia la contraseña de un admin existente")
    args = parser.parse_args()

    username = admins.normalize(args.username)
    existing = admins.get(username)

    if args.reset_password:
        if existing is None:
            print(f"El admin «{username}» no existe.")
            return 1
        admins.set_password(username, ask_password())
        audit.record("script", "admin.password", f"admin:{username}")
        print(f"Contraseña de «{username}» actualizada.")
        return 0

    if existing is not None:
        print(f"El admin «{username}» ya existe. Usa --reset-password para cambiar su contraseña.")
        return 1
    try:
        admins.create(username, ask_password(), args.role)
    except ValueError as exc:
        print(exc)
        return 1
    audit.record("script", "admin.create", f"admin:{username}", {"role": args.role})
    print(f"Admin «{username}» creado con rol {args.role}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
