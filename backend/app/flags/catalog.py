"""Catálogo de flags y parámetros de la app (config v1).

Este módulo es la ÚNICA definición en el backend de:
  - qué claves existen, su tipo y sus límites (modelo Pydantic `AppConfig`);
  - sus valores por defecto, con los que la app funciona completa
    (botón "Restablecer valores por defecto" del panel);
  - cómo se muestran y editan en el panel (`FLAG_SPECS`).

El mismo modelo valida al escribir (panel) y al servir (API). Debe mantenerse en
sincronía con `docs/openapi.yaml` (#/components/schemas/AppConfig).

Dependencias entre flags: `history_enabled=false` ⇒ `history_save_enabled=false` y
`export_enabled=false`; `announcements_enabled=false` ⇒ no se muestra ningún anuncio.
Se RESUELVEN en la app (un solo lugar); el backend guarda y sirve los valores tal cual
y el panel solo lo advierte.

Claves retiradas (las versiones antiguas guardadas en Firestore las conservan, pero
`AppConfig.from_stored` las ignora): guest_mode_enabled, class_thresholds,
hidden_classes, image_max_px, jpeg_quality, history_max_items.
"""

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CONFIG_SCHEMA_VERSION = 1

ANNOUNCEMENT_SLOTS = 3          # bloques de anuncios configurables desde el panel
ANNOUNCEMENT_MAX_LEN = 500
SCORE_THRESHOLD_MIN = 0.3       # mínimo del umbral de confianza (panel, API y demo)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # --- Funciones de la app ---
    camera_enabled: bool = True
    gallery_enabled: bool = True
    history_enabled: bool = True
    history_save_enabled: bool = True
    export_enabled: bool = True
    save_to_gallery_enabled: bool = True
    about_enabled: bool = True
    tips_enabled: bool = True
    show_api_status: bool = True

    # FUTURO (Fase 5): sincronización de imágenes con Cloud Storage.
    # Hoy NO hay código detrás de esta flag ni en la app ni en el backend, y su fila
    # está oculta en el panel (FlagSpec.hidden). Por defecto false: la app no sincroniza.
    cloud_sync_enabled: bool = False

    # --- Anuncios (noticias, enlaces a formularios de opinión, etc.) ---
    # Texto plano; la app les da formato y convierte los enlaces. Un bloque vacío no se muestra.
    announcements_enabled: bool = False
    announcement_1: str = Field("", max_length=ANNOUNCEMENT_MAX_LEN)
    announcement_2: str = Field("", max_length=ANNOUNCEMENT_MAX_LEN)
    announcement_3: str = Field("", max_length=ANNOUNCEMENT_MAX_LEN)

    # --- Cuentas y acceso (efecto a partir de la Fase 3; el login será obligatorio) ---
    registration_enabled: bool = True

    # --- Mantenimiento y versión ---
    maintenance_mode: bool = False
    maintenance_message: str = Field(
        "Estamos realizando tareas de mantenimiento. Vuelve a intentarlo más tarde.",
        max_length=500,
    )
    min_app_version: str = Field("1.0", pattern=r"^\d+(\.\d+){0,3}$")

    # --- Detección ---
    score_threshold: float = Field(0.3, ge=SCORE_THRESHOLD_MIN, le=1.0)
    nms_enabled: bool = True
    nms_iou: float = Field(0.3, ge=0.0, le=1.0)
    max_detections: int = Field(100, ge=3, le=100)

    # --- Red ---
    request_timeout_s: int = Field(60, ge=5, le=120)
    retries: int = Field(0, ge=0, le=5)

    # --- Web. false hasta que el admin la active: expone inferencia pública ---
    demo_enabled: bool = False

    @field_validator("announcement_1", "announcement_2", "announcement_3", "maintenance_message")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @classmethod
    def from_stored(cls, data: Optional[Mapping[str, Any]]) -> "AppConfig":
        """Lee una config guardada tolerando claves obsoletas (se ignoran), claves
        nuevas ausentes (toman su valor por defecto) y valores numéricos fuera de los
        límites actuales (se recortan al límite; p. ej. un score_threshold de 0.2
        publicado antes de que el mínimo fuera 0.3 se lee como 0.3)."""
        data = data or {}
        known = {k: _clamp(k, v) for k, v in data.items() if k in cls.model_fields}
        return cls.model_validate(known)


def _clamp(key: str, value: Any) -> Any:
    """Recorta un valor numérico guardado a los límites ge/le actuales del campo."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    for meta in AppConfig.model_fields[key].metadata:
        ge, le = getattr(meta, "ge", None), getattr(meta, "le", None)
        if ge is not None and value < ge:
            value = ge
        if le is not None and value > le:
            value = le
    return value


DEFAULT_CONFIG = AppConfig()


# --------------------------------------------------------------------------- #
# Metadatos para el panel
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FlagSpec:
    key: str
    label: str
    help: str
    group: str
    kind: str  # bool | textarea | float | int | version
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    depends_on: Optional[str] = None
    future: bool = False   # sin efecto todavía
    hidden: bool = False   # no se muestra en el panel; al guardar se conserva el valor publicado
    max_length: Optional[int] = None


GROUPS = [
    "Funciones de la app",
    "Anuncios",
    "Cuentas y acceso",
    "Mantenimiento y versión",
    "Detección",
    "Red",
    "Web",
]

# Ancla e icono (Material Symbols, ver static/img/icons.svg) de cada grupo en el panel
GROUP_UI = {
    "Funciones de la app": ("funciones", "apps"),
    "Anuncios": ("anuncios", "campaign"),
    "Cuentas y acceso": ("cuentas", "manage_accounts"),
    "Mantenimiento y versión": ("mantenimiento", "build"),
    "Detección": ("deteccion", "center_focus_strong"),
    "Red": ("red", "wifi"),
    "Web": ("web", "public"),
}

_ANN_HELP = ("Texto que verá el usuario; puede incluir enlaces (p. ej. un formulario de opinión). "
             f"Máx. {ANNOUNCEMENT_MAX_LEN} caracteres. Vacío = el bloque no se muestra.")

FLAG_SPECS: list[FlagSpec] = [
    FlagSpec("camera_enabled", "Cámara", "Permite escanear con la cámara.", GROUPS[0], "bool"),
    FlagSpec("gallery_enabled", "Galería", "Permite subir una imagen de la galería.", GROUPS[0], "bool"),
    FlagSpec("history_enabled", "Historial", "Muestra la pestaña de historial.", GROUPS[0], "bool"),
    FlagSpec("history_save_enabled", "Guardar en historial",
             "Guarda cada detección en el historial local.", GROUPS[0], "bool",
             depends_on="history_enabled"),
    FlagSpec("export_enabled", "Exportar historial",
             "Botón de exportación (ZIP con JSON e imágenes).", GROUPS[0], "bool",
             depends_on="history_enabled"),
    FlagSpec("save_to_gallery_enabled", "Guardar en galería",
             "Guarda las fotos en Pictures/EcoVisionSV.", GROUPS[0], "bool"),
    FlagSpec("about_enabled", "Acerca de", "Muestra la pantalla Acerca de.", GROUPS[0], "bool"),
    FlagSpec("tips_enabled", "Consejos", "Muestra consejos de reciclaje.", GROUPS[0], "bool"),
    FlagSpec("show_api_status", "Estado de la API",
             "Muestra el indicador de estado del servidor.", GROUPS[0], "bool"),
    # FUTURO (Fase 5): oculta en el panel mientras no haya sincronización implementada.
    # Para mostrarla de nuevo basta con quitar hidden=True (la plantilla ya la soporta).
    FlagSpec("cloud_sync_enabled", "Sincronización en la nube",
             "Respaldo de imágenes en Cloud Storage. Aún sin implementar (Fase 5): "
             "cambiarla no tiene efecto.", GROUPS[0], "bool", future=True, hidden=True),

    FlagSpec("announcements_enabled", "Sección de anuncios",
             "Muestra en la app los anuncios con texto. Apagada, no se ve ninguno.",
             GROUPS[1], "bool"),
    FlagSpec("announcement_1", "Anuncio 1", _ANN_HELP, GROUPS[1], "textarea",
             depends_on="announcements_enabled", max_length=ANNOUNCEMENT_MAX_LEN),
    FlagSpec("announcement_2", "Anuncio 2", _ANN_HELP, GROUPS[1], "textarea",
             depends_on="announcements_enabled", max_length=ANNOUNCEMENT_MAX_LEN),
    FlagSpec("announcement_3", "Anuncio 3", _ANN_HELP, GROUPS[1], "textarea",
             depends_on="announcements_enabled", max_length=ANNOUNCEMENT_MAX_LEN),

    FlagSpec("registration_enabled", "Registro de usuarios",
             "Permite crear cuentas nuevas desde la app (Fase 3).", GROUPS[2], "bool"),

    FlagSpec("maintenance_mode", "Modo mantenimiento",
             "La app muestra el mensaje de mantenimiento y bloquea su uso.", GROUPS[3], "bool"),
    FlagSpec("maintenance_message", "Mensaje de mantenimiento", "Máx. 500 caracteres.",
             GROUPS[3], "textarea", max_length=500),
    FlagSpec("min_app_version", "Versión mínima de la app",
             "versionName mínima admitida, p. ej. 1.0 o 1.2.3.", GROUPS[3], "version"),

    FlagSpec("score_threshold", "Umbral de confianza",
             "Se envía como ?umbral= (0.3–1).", GROUPS[4], "float", SCORE_THRESHOLD_MIN, 1, 0.01),
    FlagSpec("nms_enabled", "Filtro NMS", "Elimina detecciones solapadas en la app.",
             GROUPS[4], "bool"),
    FlagSpec("nms_iou", "IoU del NMS",
             "Se elimina una caja si su solapamiento con otra de más confianza es MAYOR que "
             "este valor. 0 = elimina cualquier solapamiento; 1 = no elimina ninguna.",
             GROUPS[4], "float", 0, 1, 0.01, depends_on="nms_enabled"),
    FlagSpec("max_detections", "Máximo de detecciones",
             "Detecciones mostradas por imagen (3–100).", GROUPS[4], "int", 3, 100, 1),

    FlagSpec("request_timeout_s", "Timeout de petición (s)", "Tiempo máximo de espera de /predict.",
             GROUPS[5], "int", 5, 120, 1),
    FlagSpec("retries", "Reintentos", "Reintentos de /predict ante fallo de red.",
             GROUPS[5], "int", 0, 5, 1),

    FlagSpec("demo_enabled", "Demo web",
             "Página pública /demo para probar el modelo sin registro.", GROUPS[6], "bool"),
]

SPECS_BY_KEY = {s.key: s for s in FLAG_SPECS}
VISIBLE_SPECS = [s for s in FLAG_SPECS if not s.hidden]
assert set(SPECS_BY_KEY) == set(AppConfig.model_fields), "FLAG_SPECS y AppConfig no coinciden"


# --------------------------------------------------------------------------- #
# Conversión formulario <-> config
# --------------------------------------------------------------------------- #
def to_form_values(cfg: AppConfig) -> dict[str, Any]:
    """Valores para pintar el formulario del panel."""
    return cfg.model_dump()


def _num(v: Any) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


def _error_es(spec: Optional[FlagSpec], err: Mapping[str, Any]) -> str:
    """Mensaje en español para un error de validación de Pydantic."""
    etype, ctx = err.get("type", ""), err.get("ctx") or {}
    if etype == "string_pattern_mismatch" and spec and spec.kind == "version":
        return "Formato no válido: usa números separados por puntos, p. ej. 1.0 o 1.2.3."
    if etype == "greater_than_equal":
        return f"Debe ser mayor o igual que {_num(ctx.get('ge'))}."
    if etype == "less_than_equal":
        return f"Debe ser menor o igual que {_num(ctx.get('le'))}."
    if etype in ("int_parsing", "int_from_float", "int_type"):
        return "Debe ser un número entero."
    if etype in ("float_parsing", "float_type"):
        return "Debe ser un número."
    if etype == "string_too_long":
        return f"Máximo {ctx.get('max_length')} caracteres."
    if etype == "missing":
        return "Campo obligatorio."
    return "Valor no válido."


def parse_form(form: Mapping[str, Any], current: Optional[AppConfig] = None
               ) -> tuple[Optional[AppConfig], dict[str, Any], dict[str, str]]:
    """Convierte el formulario del panel en `AppConfig`.

    `current` es la config publicada: las claves ocultas en el panel (`hidden`)
    conservan su valor en vez de volver al valor por defecto.
    Devuelve (config o None, valores crudos para repintar, errores por campo).
    """
    base = (current or DEFAULT_CONFIG).model_dump()
    raw: dict[str, Any] = {}
    values: dict[str, Any] = {}

    for spec in FLAG_SPECS:
        if spec.hidden:
            raw[spec.key] = values[spec.key] = base[spec.key]
        elif spec.kind == "bool":
            raw[spec.key] = values[spec.key] = spec.key in form
        else:
            text = str(form.get(spec.key, "")).strip()
            values[spec.key] = text
            raw[spec.key] = text.replace(",", ".") if spec.kind == "float" else text

    try:
        return AppConfig.model_validate(raw), values, {}
    except ValidationError as exc:
        errors: dict[str, str] = {}
        for err in exc.errors():
            key = str(err["loc"][0]) if err["loc"] else "_"
            errors.setdefault(key, _error_es(SPECS_BY_KEY.get(key), err))
        return None, values, errors


def diff(old: AppConfig, new: AppConfig) -> dict[str, list[Any]]:
    """Claves que cambian: {clave: [antes, después]} (para la auditoría)."""
    a, b = old.model_dump(mode="json"), new.model_dump(mode="json")
    return {k: [a.get(k), b[k]] for k in b if a.get(k) != b[k]}
