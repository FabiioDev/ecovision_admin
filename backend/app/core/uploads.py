"""Validación de imágenes subidas (demo web y POST /api/v1/predict).

Solo comprueba lo barato (tipo declarado y tamaño); la inferencia rechaza lo que no
pueda decodificar. Nunca se guarda ni se registra la imagen.
"""

from fastapi import UploadFile

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}


class ImagenInvalida(Exception):
    """`status` es el código HTTP que se devuelve al cliente."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


async def leer_imagen(file: UploadFile, max_mb: float) -> bytes:
    """Bytes de la imagen. Lee como mucho max_mb + 1 byte: no carga archivos enormes."""
    if (file.content_type or "").lower() not in ALLOWED_TYPES:
        raise ImagenInvalida(415, "Formato no admitido. Usa una imagen JPEG, PNG o WebP.")
    max_bytes = int(max_mb * 1024 * 1024)
    data = await file.read(max_bytes + 1)
    if not data:
        raise ImagenInvalida(400, "No se recibió ninguna imagen.")
    if len(data) > max_bytes:
        raise ImagenInvalida(413, f"La imagen supera {max_mb:g} MB.")
    return data
