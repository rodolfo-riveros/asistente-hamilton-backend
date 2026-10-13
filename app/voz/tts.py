"""
Síntesis de voz (TTS) en la nube con Azure AI Speech.

La app usa por defecto la voz del propio teléfono (`expo-speech`). Esto es la
alternativa opcional "voz neuronal": la app manda el texto ya limpio de
markdown, aquí se envuelve en SSML con una prosodia calmada y Azure devuelve un
MP3 que la app reproduce. Si algo falla (o tarda > 8 s), la app vuelve a la voz
del teléfono, así que los errores tienen que ser rápidos y claros.

REST: `POST https://{region}.tts.speech.microsoft.com/cognitiveservices/v1`
con `Ocp-Apim-Subscription-Key` (la clave sólo vive en el servidor).
"""

from __future__ import annotations

import logging
import re
from typing import Literal
from xml.sax.saxutils import escape, quoteattr

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

Ritmo = Literal["pausada", "normal"]

# La app abandona a los 8 s: no tiene sentido esperar más a Azure.
TIMEOUT = httpx.Timeout(7.0, connect=3.0)

FORMATO_SALIDA = "audio-24khz-48kbitrate-mono-mp3"

# Límite de caracteres por petición: una respuesta normal del asistente cabe de
# sobra y evita gastar la cuota gratuita (F0 ≈ 0,5 M caracteres/mes) en abusos.
MAX_CARACTERES = 1500

# Prosodia calmada. Azure acepta porcentajes relativos en `rate` y `pitch`.
PROSODIA: dict[str, dict[str, str]] = {
    "pausada": {"rate": "-8%", "pitch": "-2%"},
    "normal": {"rate": "0%", "pitch": "-2%"},
}

# Respiro extra al final de cada oración (además del natural de la voz).
SILENCIO_ENTRE_ORACIONES = "250ms"

# Nombres de voz neuronal de Azure: "es-PE-CamilaNeural", "es-MX-DaliaNeural"...
# Sólo español: la app no debería pedir otra cosa y así no se inyecta nada raro
# en el atributo `name` del SSML.
VOZ_RE = re.compile(r"^es-[A-Z]{2}-[A-Za-z]+Neural$")

# Caracteres de control que XML 1.0 no admite (el SSML se rechazaría entero).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_cliente: httpx.AsyncClient | None = None


class TTSError(RuntimeError):
    def __init__(self, mensaje: str, status_code: int = 502) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.status_code = status_code


class TTSNoConfigurado(TTSError):
    def __init__(self) -> None:
        super().__init__(
            "La voz en la nube no está configurada en el servidor "
            "(faltan AZURE_SPEECH_KEY o AZURE_SPEECH_REGION).",
            status_code=503,
        )


def voz_valida(voz: str) -> bool:
    return bool(VOZ_RE.match(voz))


def _url() -> str:
    region = settings.azure_speech_region.strip().lower()
    return f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"


def _get_cliente() -> httpx.AsyncClient:
    global _cliente
    if _cliente is None:
        _cliente = httpx.AsyncClient(timeout=TIMEOUT)
    return _cliente


async def cerrar_cliente() -> None:
    global _cliente
    if _cliente is not None:
        await _cliente.aclose()
        _cliente = None


def _idioma_de(voz: str) -> str:
    """`es-PE-CamilaNeural` -> `es-PE`."""
    partes = voz.split("-")
    return "-".join(partes[:2]) if len(partes) >= 2 else "es-ES"


def construir_ssml(texto: str, voz: str, ritmo: Ritmo = "pausada") -> str:
    """SSML con la voz, la prosodia calmada y el texto escapado para XML."""
    limpio = _CONTROL_RE.sub(" ", texto).strip()
    prosodia = PROSODIA.get(ritmo, PROSODIA["pausada"])
    idioma = _idioma_de(voz)
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="https://www.w3.org/2001/mstts" '
        f"xml:lang={quoteattr(idioma)}>"
        f"<voice name={quoteattr(voz)}>"
        f'<mstts:silence type="Sentenceboundary" value="{SILENCIO_ENTRE_ORACIONES}"/>'
        f"<prosody rate={quoteattr(prosodia['rate'])} pitch={quoteattr(prosodia['pitch'])}>"
        f"{escape(limpio)}"
        "</prosody></voice></speak>"
    )


async def sintetizar(texto: str, voz: str | None = None, ritmo: Ritmo = "pausada") -> bytes:
    """Devuelve el MP3 (bytes) del texto. Lanza `TTSError` si falla."""
    if not settings.tts_habilitado:
        raise TTSNoConfigurado()

    texto = texto.strip()
    if not texto:
        raise TTSError("No hay texto para leer.", status_code=400)
    if len(texto) > MAX_CARACTERES:
        raise TTSError(
            f"El texto supera los {MAX_CARACTERES} caracteres de la voz en la nube.",
            status_code=413,
        )

    voz = voz or settings.tts_voz
    if not voz_valida(voz):
        raise TTSError("Voz no admitida.", status_code=422)

    try:
        respuesta = await _get_cliente().post(
            _url(),
            content=construir_ssml(texto, voz, ritmo).encode("utf-8"),
            headers={
                "Ocp-Apim-Subscription-Key": settings.azure_speech_key.strip(),
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": FORMATO_SALIDA,
                "User-Agent": "asistente-hamilton-backend",
            },
        )
    except httpx.TimeoutException as exc:
        raise TTSError("La voz en la nube tardó demasiado.", status_code=504) from exc
    except httpx.HTTPError as exc:
        logger.warning("No se pudo contactar con Azure Speech: %s", exc)
        raise TTSError("No se pudo contactar con el servicio de voz.") from exc

    if not respuesta.is_success:
        # Nunca se reenvía un 401/403 de Azure tal cual: la app lo leería como
        # "tu sesión caducó" y cerraría la sesión de la persona.
        logger.warning("Azure Speech respondió %s: %s", respuesta.status_code, respuesta.text[:300])
        if respuesta.status_code == 429:
            raise TTSError("La voz en la nube está saturada; prueba en un momento.", 503)
        if respuesta.status_code in (401, 403):
            raise TTSError("La credencial de la voz en la nube no es válida.", 502)
        raise TTSError(f"El servicio de voz respondió {respuesta.status_code}.", 502)

    if not respuesta.content:
        raise TTSError("El servicio de voz devolvió un audio vacío.")
    return respuesta.content
