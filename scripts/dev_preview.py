"""Vista previa LOCAL del panel sin GCP: Firestore y Firebase Auth se sustituyen por
datos en memoria (se pierden al cerrar). Solo para ver y probar la interfaz.

La demo (/demo) usa una inferencia FALSA con detecciones fijas, para ver la interfaz
sin llamar al modelo real.

NO usar en producción ni desplegar: no hay persistencia ni seguridad real.

Uso (desde la raíz de ecovision_admin, con las dependencias de backend/requirements.txt):
    python scripts/dev_preview.py                 # admin superadmin
    python scripts/dev_preview.py --role lector   # ver el panel como lector
    python scripts/dev_preview.py --demo          # arranca con demo_enabled=true
Luego abre http://localhost:8081/admin  (usuario: admin / contraseña: admin-preview)
y http://localhost:8081/demo
"""

import argparse
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

parser = argparse.ArgumentParser(description="Vista previa del panel con datos en memoria")
parser.add_argument("--role", choices=("lector", "editor", "superadmin"), default="superadmin")
parser.add_argument("--port", type=int, default=8081)
parser.add_argument("--demo", action="store_true", help="arrancar con la flag demo_enabled activada")
args = parser.parse_args()

# Debe fijarse antes de importar la app (la config se lee una sola vez)
os.environ.update({
    "ROLE": "all",
    "SECRET_KEY": secrets.token_urlsafe(48),
    "COOKIE_SECURE": "false",
    "DOCS_ENABLED": "true",
    "CONFIG_CACHE_TTL_S": "0",
    "CONFIG_REQUIRE_AUTH": "false",
    "INFERENCE_URL": "http://inferencia-falsa.invalid",  # nunca se llama (ver abajo)
    "INFERENCE_USE_ID_TOKEN": "false",
})

from app.core.security import hash_password  # noqa: E402
from app.flags.catalog import AppConfig  # noqa: E402
from app.services import admins, audit, config_store, firestore, inference, users  # noqa: E402

ADMIN_USER, ADMIN_PASS = "admin", "admin-preview"
NOW = datetime.now(timezone.utc)


def _no_gcp(*_a, **_k):
    raise RuntimeError("vista previa: no hay conexión a GCP")


firestore.get_db = _no_gcp

# ---- admins ---- #
_ADMIN = admins.Admin(ADMIN_USER, args.role, True, hash_password(ADMIN_PASS))
admins.get = lambda u: _ADMIN if admins.normalize(u) == ADMIN_USER else None
admins.record_login = lambda u: None
admins.update_password_hash = lambda u, h: None

# ---- auditoría ---- #
_LOG: list[dict] = []


def _record(actor, action, target="", details=None, ip=""):
    _LOG.insert(0, {"id": str(len(_LOG)), "ts": datetime.now(timezone.utc), "actor": actor,
                    "action": action, "action_label": audit.ACTION_LABELS.get(action, action),
                    "target": target, "details": details or {}, "ip": ip})


audit.record = _record
audit.list_entries = lambda limit=50, cursor=None: (_LOG[:limit], None)

# ---- config versionada ---- #
_CFG = {"cur": config_store.ConfigSnapshot(AppConfig(demo_enabled=args.demo), 0, None, None),
        "versions": {}}


def _save(cfg, actor, action, note=""):
    prev = _CFG["cur"]
    v, now = prev.version + 1, datetime.now(timezone.utc)
    _CFG["cur"] = config_store.ConfigSnapshot(cfg, v, now, actor)
    _CFG["versions"][v] = {"config": cfg.model_dump(mode="json"), "version": v, "created_at": now,
                           "created_by": actor, "action": action, "note": note[:200]}
    return _CFG["cur"], prev.config


config_store.get_current = lambda: _CFG["cur"]
config_store.save = _save
config_store.get_version = lambda v: _CFG["versions"].get(v)
config_store.list_versions = lambda limit=20: sorted(
    _CFG["versions"].values(), key=lambda x: -x["version"])[:limit]

# ---- usuarios de la app ---- #
_USERS: dict[str, users.AppUser] = {}
_PROFILES: dict[str, dict] = {}


def _add(email, name, providers, days, disabled=False):
    uid = secrets.token_hex(14)
    _USERS[uid] = users.AppUser(uid, email, name, disabled, True, providers,
                                NOW - timedelta(days=days), NOW - timedelta(hours=days))
    return _USERS[uid]


_add("ana@example.com", "Ana López", ["Correo"], 12)
_add("carlos@example.com", "Carlos Pérez", ["Google"], 5)
_add("maria@example.com", None, ["Correo"], 2, disabled=True)


def _get(uid):
    return _USERS.get(uid)


def _create(email, password, display_name, actor):
    email = email.strip().lower()
    if any(u.email == email for u in _USERS.values()):
        raise users.UserError("Ya existe una cuenta con ese correo.")
    u = _add(email, display_name.strip() or None, ["Correo"], 0)
    u.last_sign_in = None
    _PROFILES[u.uid] = {"created_by": f"admin:{actor}"}
    return u


def _update(uid, email, display_name):
    u = _USERS.get(uid)
    if u is None:
        raise users.UserError("El usuario no existe.")
    new = users.AppUser(**{**vars(u), "email": email.strip() or u.email,
                           "display_name": display_name.strip() or None})
    _USERS[uid] = new
    return new


def _set_disabled(uid, disabled):
    _USERS[uid].disabled = disabled


users.list_page = lambda token=None: (sorted(_USERS.values(), key=lambda u: u.created_at, reverse=True), None)
users.search = lambda q: [u for u in _USERS.values() if q.strip().lower() in ((u.email or ""), u.uid)]
users.get = _get
users.get_profile = lambda uid: _PROFILES.get(uid, {})
users.create = _create
users.update = _update
users.set_password = lambda uid, pw: None
users.set_disabled = _set_disabled
users.delete = lambda uid: (_USERS.pop(uid, None), _PROFILES.pop(uid, None))

# ---- inferencia falsa para la demo ---- #
_FAKE = [  # dos cajas casi iguales para ver el NMS, y una de baja confianza
    ("Plástico", 1, 0.91, [0.12, 0.18, 0.46, 0.80]),
    ("Plástico", 1, 0.62, [0.14, 0.20, 0.47, 0.78]),
    ("Metal", 2, 0.84, [0.55, 0.30, 0.82, 0.62]),
    ("Cartón", 4, 0.47, [0.60, 0.66, 0.95, 0.96]),
    ("Vidrio", 5, 0.22, [0.05, 0.05, 0.20, 0.25]),
]


async def _fake_predict(data, filename, content_type, umbral):
    import asyncio
    await asyncio.sleep(0.6)
    w = h = 640
    dets = [{"categoria": c, "clase_id": i, "confianza": s, "bbox_norm": b,
             "bbox_px": [int(b[0] * w), int(b[1] * h), int(b[2] * w), int(b[3] * h)]}
            for c, i, s, b in _FAKE if s >= umbral]
    return {"ancho": w, "alto": h, "umbral": umbral, "latencia_ms": 480.0, "detecciones": dets}


async def _fake_health():
    return {"conectado": True, "clases": ["Plástico", "Metal", "Papel", "Cartón", "Vidrio", "Orgánico"]}


inference.predict = _fake_predict
inference.health = _fake_health

if __name__ == "__main__":
    import uvicorn

    from app.main import app

    print("\n  Vista previa SIN GCP (datos en memoria, se pierden al cerrar)")
    print(f"  Panel:  http://localhost:{args.port}/admin")
    print(f"  Login:  {ADMIN_USER} / {ADMIN_PASS}   (rol: {args.role})")
    print(f"  Demo:   http://localhost:{args.port}/demo   (inferencia falsa; demo_enabled={args.demo})")
    print(f"  API:    http://localhost:{args.port}/api/v1/config   ·   docs: /docs\n")
    uvicorn.run(app, host="127.0.0.1", port=args.port)
