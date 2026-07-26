"""
Router del histórico de evaluaciones: GET /evaluaciones.

Devuelve las evaluaciones del usuario autenticado, más reciente primero, en el
mismo contrato que la app ya usa para pintar la pantalla "Histórico".
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.config import settings
from app.db import repositorio, supabase
from app.deps import Usuario, usuario_actual
from app.routers.chat import ResultadoResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evaluaciones", tags=["evaluaciones"])


@router.get("", response_model=list[ResultadoResponse])
async def listar(
    usuario: Usuario = Depends(usuario_actual),
    limite: int = Query(default=50, ge=1, le=200),
) -> list[ResultadoResponse]:
    if not settings.supabase_habilitado:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El histórico no está disponible: Supabase no está configurado.",
        )

    try:
        filas = await repositorio.listar_evaluaciones(usuario.id, limite=limite)
    except supabase.SupabaseError as exc:
        logger.exception("Error consultando el histórico del usuario %s", usuario.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo consultar tu histórico en este momento.",
        ) from exc

    return [ResultadoResponse(**fila) for fila in filas]
