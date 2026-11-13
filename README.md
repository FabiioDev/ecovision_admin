# EcoVision — backend y panel de administración

Servicio FastAPI de EcoVision SV (app Android que detecta residuos con un modelo
EfficientDet D1). Se despliega en Google Cloud Run y reúne:

- **API de la app** (`/api/v1`): configuración remota y feature flags (`GET /api/v1/config`
  con `ETag`), configuración pública (mantenimiento y versión mínima), detección
  (`POST /api/v1/predict`, proxy hacia la inferencia con token de Firebase) y estado del
  servicio (`GET /api/v1/health`, que además despierta la inferencia).
- **Panel de administración** (`/admin`): login de administradores, flags y parámetros con
  historial de cambios (restaurar y restablecer), auditoría y gestión de usuarios de
  Firebase Authentication.
- **Demo web** (`/demo`): prueba pública sin registro (tomar o subir una foto y ver las
  detecciones). Apagada por defecto con la flag `demo_enabled`.
- **Descarga del APK** (`/descargar`): página pública con la versión, el SHA-256 y las
  instrucciones de instalación; `/descargar/apk` redirige al archivo en GitHub Releases.
  Apagada por defecto con la flag `download_enabled`; la versión, el enlace y el SHA-256 se
  editan en el panel (grupo "Distribución").

El backend **no ejecuta el modelo**: reenvía la imagen al servicio de inferencia, que se
versiona y despliega por separado (no forma parte de este repositorio).

## Estructura

```
backend/
├── app/
│   ├── main.py        monta los routers según ROLE
│   ├── core/          configuración, seguridad, Firebase, límites de peticiones
│   ├── api/           /api/v1
│   ├── admin/         /admin (sesión por cookie, CSRF, roles)
│   ├── demo/          /demo (plantilla, JS y CSS propios)
│   ├── descarga/      /descargar (plantilla y CSS propios, sin JavaScript)
│   ├── flags/         catálogo de flags (catalog.py)
│   ├── services/      Firestore, config versionada, auditoría, usuarios, inferencia
│   ├── templates/     plantillas Jinja2 del panel
│   └── static/        CSS, fuentes e iconos
├── tests/             pruebas (pytest; sin GCP)
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt
└── .env.example
docs/openapi.yaml      contrato de la API (fuente de verdad)
scripts/
├── create_admin.py    crea o cambia la contraseña de un administrador
└── dev_preview.py     vista previa local sin GCP (datos en memoria)
```

## Requisitos

- Python 3.12
- Un proyecto de Google Cloud con Firebase, Firestore (modo nativo) y Firebase
  Authentication (proveedor correo/contraseña).
- Credenciales locales: `gcloud auth application-default login`.

## Desarrollo local

```
cd backend
python -m venv .venv
.venv\Scripts\activate                 # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env                 # rellenar GOOGLE_CLOUD_PROJECT y SECRET_KEY
uvicorn app.main:app --reload --port 8081
```

- Panel: http://localhost:8081/admin
- Config: http://localhost:8081/api/v1/config
- Demo: http://localhost:8081/demo (requiere `INFERENCE_URL` y la flag `demo_enabled`)

Primer administrador (desde la raíz del repo; la contraseña se pide por consola):

```
python scripts/create_admin.py --username <usuario> --role superadmin
```

Vista previa sin GCP (Firestore, Firebase Auth e inferencia simulados; solo para revisar
la interfaz, nunca para desplegar):

```
python scripts/dev_preview.py --demo
```

## Pruebas

Sin GCP: Firestore, Firebase Auth y la inferencia se simulan.

```
cd backend
pip install -r requirements-dev.txt
python -m pytest
```

`tests/` no se sube a Cloud Build ni entra en la imagen (`.gcloudignore`, `.dockerignore`).

## Variables de entorno

Todas están descritas en `backend/.env.example`. Las principales:

| Variable | Uso |
|---|---|
| `ROLE` | `all` (defecto), `api` (`/api/v1` + `/demo`) o `admin` (`/admin`). Permite dividir el servicio sin cambiar código. |
| `GOOGLE_CLOUD_PROJECT` | Proyecto de Firestore y Firebase Auth. |
| `SECRET_KEY` | Firma de la cookie del panel (mín. 32 caracteres). En producción, desde Secret Manager. |
| `COOKIE_SECURE` | `true` en Cloud Run (https), `false` en local. |
| `CONFIG_CACHE_TTL_S` | Segundos que `/api/v1/config` se sirve desde memoria. |
| `CONFIG_REQUIRE_AUTH` | Exigir token de Firebase en `/api/v1/config`. |
| `INFERENCE_URL` | URL del servicio de inferencia (sin barra final). |
| `INFERENCE_USE_ID_TOKEN` | Llamar a la inferencia con ID token de la cuenta de servicio (obligatorio si es privada). |
| `INFERENCE_TIMEOUT_S` | Tope de espera a la inferencia (115). La espera real es `request_timeout_s − 3` (flag del panel), para responder antes que la app. |
| `PREDICT_*` | Tamaño máximo de imagen y límite de detecciones por usuario de `/api/v1/predict`. |
| `DEMO_*` | Tamaño máximo y límites de peticiones de la demo. |
| `DOWNLOAD_RATE_*` | Límite por IP de `/descargar/apk`. |

Nunca se commitean `.env`, claves ni JSON de cuentas de servicio.

## Publicar una versión del APK

1. Generar y firmar el APK en el repo de la app (`./gradlew assembleRelease`).
2. Crear el release en GitHub con el APK adjunto (p. ej. `gh release create v1.0.0 EcoVisionSV-1.0.0.apk`).
3. En el panel, grupo "Distribución": versión, enlace de descarga del release y SHA-256
   (`certutil -hashfile EcoVisionSV-1.0.0.apk SHA256` en Windows); activar `download_enabled`.
4. Opcional: un anuncio en la app con el enlace `https://<backend>/descargar`.

El archivo de GitHub es público: apagar `download_enabled` quita el enlace del backend, pero
no impide descargarlo a quien ya tenga la URL de GitHub.

## Despliegue en Cloud Run

Se despliega solo la carpeta `backend/` (Cloud Build construye la imagen con su
`Dockerfile`; `.gcloudignore` evita subir `.env` y `.venv`):

```
gcloud run deploy <SERVICIO> --source backend --region <REGION> \
  --service-account <CUENTA_DE_SERVICIO> \
  --env-vars-file <archivo-de-variables.yaml> \
  --set-secrets SECRET_KEY=<SECRETO>:latest \
  --allow-unauthenticated --min-instances 0 --max-instances 2
```

- `min-instances` en 0: sin costo fijo.
- La cuenta de servicio necesita `roles/datastore.user`, `roles/firebaseauth.admin` y
  `roles/secretmanager.secretAccessor` sobre el secreto, y `roles/run.invoker` sobre el
  servicio de inferencia (que puede quedar privado con `--no-allow-unauthenticated`).
- Timeouts: el `--timeout` del backend en Cloud Run (300 s por defecto) debe ser mayor que
  `INFERENCE_TIMEOUT_S`.
- Las reglas de seguridad de Firestore deben negar todo acceso de cliente: el backend usa
  el SDK de administrador, que no pasa por las reglas.

## Firestore

| Colección | Contenido |
|---|---|
| `admins/{usuario}` | administradores del panel (contraseña con hash argon2id) |
| `app_config/current` | config publicada |
| `app_config/current/versions/{N}` | historial de cambios de la config |
| `users/{uid}` | perfil de usuarios de la app |
| `audit_log` | auditoría de las acciones del panel |

## Licencia

GNU Affero General Public License v3. Ver `LICENSE`.
