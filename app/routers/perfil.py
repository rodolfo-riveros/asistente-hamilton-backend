"""
Router del perfil del usuario.

- `GET/PUT /perfil` — datos personales que la app muestra y que el asistente usa
  como memoria (nombre, edad, ciudad, ocupación, bio).
- `POST /perfil/foto` — sube la foto al bucket `avatares` de Supabase Storage y
  guarda la URL pública en el perfil.

Las actividades vivían aquí en la v2; ahora tienen su propio router
(`app/routers/actividades.py`), según el `CONTRATO_V3.md`.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.config import settings
from app.db import repositorio, supabase
from app.deps import Usuario, usuario_actual

logger = logging.getLogger(__name__)

router = APIRouter(tags=["perfil"])

BUCKET_AVATARES = "avatares"
MAX_FOTO_BYTES = 5 * 1024 * 1024
TIPOS_IMAGEN = {"image/jpeg", "image/png", "image/webp"}


class PerfilResponse(BaseModel):
    id: str
    nombre: str = ""
    email: str | None = None
    foto_url: str | None = None
    edad: int | None = None
    ciudad: str | None = None
    ocupacion: str | None = None
    bio: str | None = None


class PerfilUpdate(BaseModel):
    nombre: str | None = Field(default=None, max_length=120)
    edad: int | None = Field(default=None, ge=10, le=120)
    ciudad: str | None = Field(default=None, max_length=120)
    ocupacion: str | None = Field(default=None, max_length=120)
    bio: str | None = Field(default=None, max_length=500)


def _exigir_supabase() -> None:
    if not settings.supabase_habilitado:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Esta función necesita Supabase configurado.",
        )


@router.get("/perfil", response_model=PerfilResponse)
async def ver_perfil(usuario: Usuario = Depends(usuario_actual)) -> PerfilResponse:
    _exigir_supabase()
    perfil = await repositorio.perfil_completo(usuario.id)
    if perfil is None:
        # El perfil se crea al registrarse; si falta, se rehace al vuelo.
        await repositorio.upsert_perfil(usuario.id, usuario.nombre, usuario.email)
        perfil = await repositorio.perfil_completo(usuario.id) or {}
    return PerfilResponse(**{**perfil, "id": usuario.id})


@router.put("/perfil", response_model=PerfilResponse)
async def editar_perfil(
    cambios: PerfilUpdate, usuario: Usuario = Depends(usuario_actual)
) -> PerfilResponse:
    _exigir_supabase()
    try:
        # Sólo los campos que la app mandó; `null` o "" borran el dato (el
        # nombre nunca se borra: el asistente lo necesita para dirigirse a ti).
        enviados = cambios.model_dump(exclude_unset=True)
        datos = {k: (v.strip() if isinstance(v, str) else v) for k, v in enviados.items()}
        datos = {k: (None if v == "" else v) for k, v in datos.items()}
        if datos.get("nombre", "") is None:
            datos.pop("nombre")
        perfil = await repositorio.actualizar_perfil(usuario.id, datos, permitir_nulos=True)
    except supabase.SupabaseError as exc:
        raise HTTPException(status_code=502, detail="No se pudo guardar tu perfil.") from exc

    return PerfilResponse(**{**(perfil or {}), "id": usuario.id})


@router.post("/perfil/foto", response_model=PerfilResponse)
async def subir_foto(
    foto: UploadFile = File(..., description="Imagen jpeg, png o webp"),
    usuario: Usuario = Depends(usuario_actual),
) -> PerfilResponse:
    _exigir_supabase()

    contenido = await foto.read()
    if len(contenido) > MAX_FOTO_BYTES:
        raise HTTPException(status_code=413, detail="La imagen supera los 5 MB.")

    tipo = (foto.content_type or "").lower()
    if tipo not in TIPOS_IMAGEN:
        raise HTTPException(status_code=415, detail="Formato no admitido: usa JPG, PNG o WEBP.")

    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[tipo]
    # Nombre nuevo en cada subida: evita que el CDN sirva la foto anterior.
    ruta = f"{usuario.id}/{uuid.uuid4().hex}.{extension}"

    try:
        url = await supabase.subir_archivo(BUCKET_AVATARES, ruta, contenido, tipo)
        perfil = await repositorio.actualizar_perfil(usuario.id, {"foto_url": url})
    except supabase.SupabaseError as exc:
        logger.exception("No se pudo subir la foto de %s", usuario.id)
        raise HTTPException(status_code=502, detail="No se pudo subir la imagen.") from exc

    return PerfilResponse(**{**(perfil or {}), "id": usuario.id})
