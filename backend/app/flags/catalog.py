"""Catálogo de flags y parámetros de la app (config v1).

Este módulo es la ÚNICA definición en el backend de:
  - qué claves existen, su tipo y sus límites (modelo Pydantic `AppConfig`);
  - sus valores por defecto, que reproducen el comportamiento actual de la app
    (botón "Restablecer valores por defecto" del panel);
  - cómo se muestran y editan en el panel (`FLAG_SPECS`).

El mismo modelo valida al escribir (panel) y al servir (API). Debe mantenerse en
sincronía con `docs/openapi.yaml` (#/components/schemas/AppConfig).

Dependencias entre flags: `history_enabled=false` ⇒ `history_save_enabled=false` y
`export_enabled=false`. Se RESUELVEN en la app (un solo lugar); el backend guarda y
sirve los valores tal cual y el panel solo lo advierte.
"""

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CONFIG_SCHEMA_VERSION = 1

_CATEGORY_RE = re.compile(r"^[\w\- áéíóúñÁÉÍÓÚÑ]{1,50}$")


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
    # Hoy NO hay código detrás de esta flag ni en la app ni en el backend.
    # Por defecto false: la app actual no sincroniza nada.
    cloud_sync_enabled: bool = False

    # --- Cuentas y acceso (efecto a partir de la Fase 3) ---
    registration_enabled: bool = True
    # true mientras la app no tenga login, para que siga funcionando sin cuenta.
    guest_mode_enabled: bool = True

    # --- Mantenimiento y versión ---
    maintenance_mode: bool = False
    maintenance_message: str = Field(
        "Estamos realizando tareas de mantenimiento. Vuelve a intentarlo más tarde.",
        max_length=500,
    )
    min_app_version: str = Field("1.0", pattern=r"^\d+(\.\d+){0,3}$")

    # --- Detección ---
    score_threshold: float = Field(0.3, ge=0.0, le=1.0)
    nms_enabled: bool = True
    nms_iou: float = Field(0.45, ge=0.05, le=0.95)
    max_detections: int = Field(100, ge=1, le=100)
    class_thresholds: dict[str, float] = Field(default_factory=dict)
    hidden_classes: list[str] = Field(default_factory=list)

    # --- Imagen y red ---
    image_max_px: int = Field(640, ge=256, le=2048)
    jpeg_quality: int = Field(85, ge=50, le=100)
    request_timeout_s: int = Field(60, ge=5, le=120)
    retries: int = Field(0, ge=0, le=5)

    # --- Historial ---
    history_max_items: int = Field(0, ge=0, le=100000)  # 0 = sin límite

    # --- Web (Fase 6). false: la demo aún no existe y expondría inferencia pública ---
    demo_enabled: bool = False

    @field_validator("class_thresholds")
    @classmethod
    def _check_thresholds(cls, v: dict[str, float]) -> dict[str, float]:
        for cat, thr in v.items():
            if not _CATEGORY_RE.match(cat):
                raise ValueError(f"categoría inválida: {cat!r}")
            if not 0.0 <= thr <= 1.0:
                raise ValueError(f"umbral de {cat!r} fuera de 0–1")
        return v

    @field_validator("hidden_classes")
    @classmethod
    def _check_hidden(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for cat in v:
            if not _CATEGORY_RE.match(cat):
                raise ValueError(f"categoría inválida: {cat!r}")
            if cat not in out:
                out.append(cat)
        return out

    @classmethod
    def from_stored(cls, data: Optional[Mapping[str, Any]]) -> "AppConfig":
        """Lee una config guardada tolerando claves obsoletas (se ignoran) y
        claves nuevas ausentes (toman su valor por defecto)."""
        data = data or {}
        known = {k: v for k, v in data.items() if k in cls.model_fields}
        return cls.model_validate(known)


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
    kind: str  # bool | text | textarea | float | int | version | dict_float | list_str
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    depends_on: Optional[str] = None
    future: bool = False  # sin efecto todavía


GROUPS = [
    "Funciones de la app",
    "Cuentas y acceso",
    "Mantenimiento y versión",
    "Detección",
    "Imagen y red",
    "Historial",
    "Web",
]

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
    FlagSpec("cloud_sync_enabled", "Sincronización en la nube",
             "Respaldo de imágenes en Cloud Storage. Aún sin implementar (Fase 5): "
             "cambiarla no tiene efecto.", GROUPS[0], "bool", future=True),

    FlagSpec("registration_enabled", "Registro de usuarios",
             "Permite crear cuentas nuevas desde la app (Fase 3).", GROUPS[1], "bool"),
    FlagSpec("guest_mode_enabled", "Modo invitado",
             "Permite usar la app sin iniciar sesión.", GROUPS[1], "bool"),

    FlagSpec("maintenance_mode", "Modo mantenimiento",
             "La app muestra el mensaje de mantenimiento y bloquea su uso.", GROUPS[2], "bool"),
    FlagSpec("maintenance_message", "Mensaje de mantenimiento", "Máx. 500 caracteres.",
             GROUPS[2], "textarea"),
    FlagSpec("min_app_version", "Versión mínima de la app",
             "versionName mínima admitida, p. ej. 1.0 o 1.2.3.", GROUPS[2], "version"),

    FlagSpec("score_threshold", "Umbral de confianza",
             "Se envía como ?umbral= (0–1).", GROUPS[3], "float", 0, 1, 0.01),
    FlagSpec("nms_enabled", "Filtro NMS", "Elimina detecciones solapadas en la app.",
             GROUPS[3], "bool"),
    FlagSpec("nms_iou", "IoU del NMS",
             "Solapamiento a partir del cual dos cajas son el mismo objeto.",
             GROUPS[3], "float", 0.05, 0.95, 0.01, depends_on="nms_enabled"),
    FlagSpec("max_detections", "Máximo de detecciones", "Detecciones mostradas por imagen.",
             GROUPS[3], "int", 1, 100, 1),
    FlagSpec("class_thresholds", "Umbral por categoría",
             "Una por línea: Categoria=0.5. Vacío = usar el umbral general.",
             GROUPS[3], "dict_float"),
    FlagSpec("hidden_classes", "Categorías ocultas", "Separadas por comas.",
             GROUPS[3], "list_str"),

    FlagSpec("image_max_px", "Tamaño de imagen (px)", "Lado de la imagen enviada al modelo.",
             GROUPS[4], "int", 256, 2048, 1),
    FlagSpec("jpeg_quality", "Calidad JPEG", "Compresión de la imagen enviada (50–100).",
             GROUPS[4], "int", 50, 100, 1),
    FlagSpec("request_timeout_s", "Timeout de petición (s)", "Tiempo máximo de espera de /predict.",
             GROUPS[4], "int", 5, 120, 1),
    FlagSpec("retries", "Reintentos", "Reintentos de /predict ante fallo de red.",
             GROUPS[4], "int", 0, 5, 1),

    FlagSpec("history_max_items", "Máximo de elementos en historial", "0 = sin límite.",
             GROUPS[5], "int", 0, 100000, 1),

    FlagSpec("demo_enabled", "Demo web", "Demo pública sin registro (Fase 6).", GROUPS[6], "bool"),
]

SPECS_BY_KEY = {s.key: s for s in FLAG_SPECS}
assert set(SPECS_BY_KEY) == set(AppConfig.model_fields), "FLAG_SPECS y AppConfig no coinciden"


# --------------------------------------------------------------------------- #
# Conversión formulario <-> config
# --------------------------------------------------------------------------- #
def to_form_values(cfg: AppConfig) -> dict[str, Any]:
    """Valores para pintar el formulario del panel."""
    data = cfg.model_dump()
    out: dict[str, Any] = {}
    for spec in FLAG_SPECS:
        v = data[spec.key]
        if spec.kind == "dict_float":
            out[spec.key] = "\n".join(f"{k}={val}" for k, val in v.items())
        elif spec.kind == "list_str":
            out[spec.key] = ", ".join(v)
        else:
            out[spec.key] = v
    return out


def parse_form(form: Mapping[str, Any]) -> tuple[Optional[AppConfig], dict[str, Any], dict[str, str]]:
    """Convierte el formulario del panel en `AppConfig`.

    Devuelve (config o None, valores crudos para repintar, errores por campo).
    """
    raw: dict[str, Any] = {}
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}

    for spec in FLAG_SPECS:
        if spec.kind == "bool":
            raw[spec.key] = values[spec.key] = spec.key in form
            continue

        text = str(form.get(spec.key, "")).strip()
        values[spec.key] = text

        if spec.kind == "dict_float":
            parsed: dict[str, float] = {}
            for n, line in enumerate(text.splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                cat, sep, num = line.partition("=")
                try:
                    if not sep:
                        raise ValueError
                    parsed[cat.strip()] = float(num.strip().replace(",", "."))
                except ValueError:
                    errors[spec.key] = f"Línea {n}: usa el formato Categoria=0.5"
                    break
            raw[spec.key] = parsed
        elif spec.kind == "list_str":
            raw[spec.key] = [p.strip() for p in text.split(",") if p.strip()]
        elif spec.kind == "float":
            raw[spec.key] = text.replace(",", ".")
        else:
            raw[spec.key] = text

    if errors:
        return None, values, errors
    try:
        return AppConfig.model_validate(raw), values, {}
    except ValidationError as exc:
        for err in exc.errors():
            key = str(err["loc"][0]) if err["loc"] else "_"
            errors.setdefault(key, err["msg"])
        return None, values, errors


def diff(old: AppConfig, new: AppConfig) -> dict[str, list[Any]]:
    """Claves que cambian: {clave: [antes, después]} (para la auditoría)."""
    a, b = old.model_dump(mode="json"), new.model_dump(mode="json")
    return {k: [a.get(k), b[k]] for k in b if a.get(k) != b[k]}
