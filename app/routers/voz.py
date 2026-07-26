"""
Router de voz: POST /voz/transcribir.

Recibe el audio grabado por la app y devuelve el texto. La app decide qué hacer
con él (normalmente enviarlo como mensaje de chat).

La síntesis de voz (que el asistente "hable") NO pasa por aquí: la hace el
propio teléfono con expo-speech. Es gratis, no gasta cuota, no añade latencia
de red y funciona sin conexión.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.deps import Usuario, usuario_opcional
from app.voz import stt

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
