"""
Router de voz.

- `POST /voz/transcribir`: recibe el audio grabado por la app y devuelve el
  texto. La app decide qué hacer con él (normalmente enviarlo como mensaje).
- `POST /voz/sintetizar`: voz neuronal en la nube (Azure AI Speech), OPCIONAL.
  Por defecto la asistente habla con la voz del propio teléfono (expo-speech);
  la app sólo llama aquí con `EXPO_PUBLIC_TTS_NUBE=true` y, ante cualquier
  error o si tarda más de 8 s, vuelve a la voz del teléfono.
"""

from __future__ import annotations

import logging

from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from app.deps import Usuario, usuario_actual, usuario_opcional
from app.voz import stt, tts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voz", tags=["voz"])

# 25 MB: más que suficiente para un turno hablado y evita subidas abusivas.
MAX_BYTES = 25 * 1024 * 1024


@router.post("/transcribir")
async def transcribir(
    audio: UploadFile = File(..., description="Audio grabado (m4a, mp3, wav, webm...)"),
    _usuario: Usuario | None = Depends(usuario_opcional),
) -> dict:
    contenido = await audio.read()

    if len(contenido) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="El audio es demasiado largo.")

    try:
        texto = await stt.transcribir(
            contenido,
            nombre_archivo=audio.filename or "audio.m4a",
            content_type=audio.content_type or "application/octet-stream",
        )
    except stt.STTError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.mensaje) from exc

    return {"texto": texto}


class SintetizarRequest(BaseModel):
    # El tope real lo pone `tts.MAX_CARACTERES` (413 con mensaje claro); este
    # sólo corta cuerpos absurdos antes de procesarlos.
    texto: str = Field(..., min_length=1, max_length=10_000)
    voz: str | None = Field(default=None, description="Voz de Azure, p. ej. es-PE-CamilaNeural")
    ritmo: Literal["pausada", "normal"] = "pausada"


@router.post(
    "/sintetizar",
    response_class=Response,
    responses={200: {"content": {"audio/mpeg": {}}, "description": "MP3 mono 24 kHz / 48 kbps"}},
)
async def sintetizar(
    payload: SintetizarRequest,
    _usuario: Usuario = Depends(usuario_actual),
) -> Response:
    try:
        audio = await tts.sintetizar(payload.texto, voz=payload.voz, ritmo=payload.ritmo)
    except tts.TTSError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.mensaje) from exc

    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )
