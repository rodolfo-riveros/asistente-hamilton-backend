"""
Router de actividades — sección 1 del `CONTRATO_V3.md`.

Son los planes que la persona acuerda con el asistente en el chat (o que añade
por su cuenta desde la pantalla de Actividades):

- `GET    /actividades?estado=`   — pendientes | vencidas | completadas | todas
- `POST   /actividades`           — añadir una a mano
- `PATCH  /actividades/{id}`      — marcar hecha (o desmarcarla)
- `DELETE /actividades/{id}`      — borrarla

`vencida` no se guarda en la base: lo calcula el repositorio comparando
`para_cuando` con la hora actual.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from app.config import settings
from app.db import repositorio, supabase
from app.deps import Usuario, usuario_actual

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/actividades", tags=["actividades"])

CATEGORIAS = ("autocuidado", "social", "estudio", "salud", "ocio", "general")


class ActividadResponse(BaseModel):
    id: str
    titulo: str
    nota: str | None = None
    categoria: str = "general"
    para_cuando: str | None = None
    completada: bool = False
    completada_en: str | None = None
    vencida: bool = False
    motivo_no_realizada: str | None = None
    creado_en: str | None = None


class ActividadCrear(BaseModel):
    titulo: str = Field(..., min_length=1, max_length=200)
    nota: str | None = Field(default=None, max_length=500)
    categoria: str = Field(default="general")
    para_cuando: str | None = Field(default=None, description="Fecha ISO 8601")


class ActividadPatch(BaseModel):
    completada: bool = True


def _exigir_supabase() -> None:
    if not settings.supabase_habilitado:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Esta función necesita Supabase configurado.",
        )


def _a_respuesta(fila: dict) -> ActividadResponse:
    """Se ignoran las columnas que no forman parte del contrato (sesion_id...)."""
    campos = ActividadResponse.model_fields
    return ActividadResponse(**{k: v for k, v in fila.items() if k in campos})


@router.get("", response_model=list[ActividadResponse])
async def listar(
    usuario: Usuario = Depends(usuario_actual),
    estado: str = Query(default="pendientes", pattern="^(pendientes|vencidas|completadas|todas)$"),
    limite: int = Query(default=50, ge=1, le=200),
) -> list[ActividadResponse]:
    _exigir_supabase()
    try:
        filas = await repositorio.listar_actividades(usuario.id, estado=estado, limite=limite)
    except supabase.SupabaseError as exc:
        logger.exception("No se pudieron listar las actividades de %s", usuario.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudieron consultar tus actividades.",
        ) from exc

    return [_a_respuesta(f) for f in filas]


@router.post("", response_model=ActividadResponse, status_code=status.HTTP_201_CREATED)
async def crear(
    datos: ActividadCrear, usuario: Usuario = Depends(usuario_actual)
) -> ActividadResponse:
    """Permite que la persona añada un plan por su cuenta, sin pasar por el chat."""
    _exigir_supabase()

    categoria = datos.categoria if datos.categoria in CATEGORIAS else "general"
    try:
        fila = await repositorio.crear_actividad(
            usuario_id=usuario.id,
            titulo=datos.titulo.strip(),
            nota=datos.nota,
            categoria=categoria,
            para_cuando=datos.para_cuando,
        )
    except supabase.SupabaseError as exc:
        logger.exception("No se pudo crear la actividad de %s", usuario.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo guardar la actividad.",
        ) from exc

    return _a_respuesta({**fila, "vencida": repositorio.esta_vencida(fila)})


@router.patch("/{actividad_id}", response_model=ActividadResponse)
async def completar(
    actividad_id: str,
    cambios: ActividadPatch | None = None,
    usuario: Usuario = Depends(usuario_actual),
) -> ActividadResponse:
    """Marca la actividad como hecha y fija `completada_en`."""
    _exigir_supabase()

    completada = cambios.completada if cambios is not None else True
    try:
        fila = await repositorio.marcar_actividad(usuario.id, actividad_id, completada)
    except supabase.SupabaseError as exc:
        logger.exception("No se pudo actualizar la actividad %s", actividad_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo actualizar la actividad.",
        ) from exc

    if fila is None:
        raise HTTPException(status_code=404, detail="Actividad no encontrada.")
    return _a_respuesta(fila)


@router.delete("/{actividad_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar(actividad_id: str, usuario: Usuario = Depends(usuario_actual)) -> Response:
    _exigir_supabase()
    try:
        borrada = await repositorio.eliminar_actividad(usuario.id, actividad_id)
    except supabase.SupabaseError as exc:
        logger.exception("No se pudo eliminar la actividad %s", actividad_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo eliminar la actividad.",
        ) from exc

    if not borrada:
        raise HTTPException(status_code=404, detail="Actividad no encontrada.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
