"""
Transcripción de voz a texto (STT), agnóstica de proveedor.

Habla el dialecto de OpenAI `/audio/transcriptions` (multipart con el archivo
de audio), que es el que implementan Whisper de OpenAI, Groq, faster-whisper
local y varios endpoints de NVIDIA. Cambiar de servicio = cambiar `STT_BASE_URL`
y `STT_MODEL` en el `.env`.

Si tu proveedor usa otro contrato (por ejemplo Riva por gRPC), este es el único
archivo que hay que tocar: el router y la app no saben nada de él.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(120.0, connect=10.0)

_cliente: httpx.AsyncClient | None = None


class STTError(RuntimeError):
    def __init__(self, mensaje: str, status_code: int = 502) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.status_code = status_code


class STTNoConfigurado(STTError):
    def __init__(self) -> None:
        super().__init__(
            "La transcripción de voz no está configurada en el servidor "
            "(falta STT_BASE_URL).",
            status_code=503,
        )


def _get_cliente() -> httpx.AsyncClient:
    global _cliente
    if _cliente is None:
        if not settings.stt_habilitado:
            raise STTNoConfigurado()
        _cliente = httpx.AsyncClient(
            base_url=settings.stt_base_url.rstrip("/"),
            timeout=TIMEOUT,
            headers={"Authorization": f"Bearer {settings.stt_api_key}"},
        )
    return _cliente


async def cerrar_cliente() -> None:
    global _cliente
    if _cliente is not None:
        await _cliente.aclose()
        _cliente = None


async def transcribir(
    audio: bytes,
    nombre_archivo: str = "audio.m4a",
    content_type: str = "audio/m4a",
) -> str:
    """Devuelve el texto dictado por el usuario. Lanza `STTError` si falla."""
    if not settings.stt_habilitado:
        raise STTNoConfigurado()
    if not audio:
        raise STTError("El audio llegó vacío.", status_code=400)

    datos = {"model": settings.stt_model}
    if settings.stt_idioma:
        datos["language"] = settings.stt_idioma

    try:
        respuesta = await _get_cliente().post(
            "/audio/transcriptions",
            files={"file": (nombre_archivo, audio, content_type)},
            data=datos,
        )
    except httpx.HTTPError as exc:
        raise STTError(f"No se pudo contactar con el servicio de transcripción: {exc}") from exc

    if not respuesta.is_success:
        logger.warning("STT respondió %s: %s", respuesta.status_code, respuesta.text[:300])
        raise STTError(
            f"El servicio de transcripción respondió {respuesta.status_code}.",
            status_code=502,
        )

    try:
        cuerpo = respuesta.json()
    except ValueError:
        # Algunos endpoints devuelven texto plano cuando response_format=text.
        return respuesta.text.strip()

    texto = cuerpo.get("text") if isinstance(cuerpo, dict) else None
    if not isinstance(texto, str):
        raise STTError("Respuesta inesperada del servicio de transcripción.")
    return texto.strip()
