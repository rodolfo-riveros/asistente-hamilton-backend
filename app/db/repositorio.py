"""
Persistencia de dominio sobre Supabase.

Todas las funciones filtran por `usuario_id` explícitamente: el backend usa la
service role key, que ignora RLS, así que el aislamiento entre usuarios se
garantiza aquí (ver nota en `app/db/supabase.py`).

Los errores de red/BD se propagan como `SupabaseError`; quien llama decide si
son fatales. Para escrituras "de mejor esfuerzo" (transcripción de mensajes)
usa `guardar_mensajes`, que registra el fallo y no interrumpe la conversación.
"""

from __future__ import annotations

import logging
from typing import Any

from app.db import supabase

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Perfiles
# --------------------------------------------------------------------------- #


async def upsert_perfil(usuario_id: str, nombre: str, email: str | None) -> None:
    """Crea/actualiza el perfil de aplicación asociado a un usuario de Auth."""
    await supabase.insert(
        "perfiles",
        {"id": usuario_id, "nombre": nombre, "email": email},
        upsert=True,
    )


async def obtener_perfil(usuario_id: str) -> dict | None:
    filas = await supabase.select(
        "perfiles", {"id": f"eq.{usuario_id}", "select": "id,nombre,email", "limit": 1}
    )
    return filas[0] if filas else None


# --------------------------------------------------------------------------- #
# Mensajes (transcripción)
# --------------------------------------------------------------------------- #


async def guardar_mensajes(
    sesion_id: str,
    usuario_id: str | None,
    mensajes: list[tuple[str, str]],
) -> None:
    """
    Guarda pares (rol, contenido) de un turno. Best-effort: si falla, se registra
    y la conversación continúa — perder la transcripción no debe cortar el chat.
    """
    filas = [
        {
            "sesion_id": sesion_id,
            "usuario_id": usuario_id,
            "rol": rol,
            "contenido": contenido,
        }
        for rol, contenido in mensajes
        if contenido
    ]
    if not filas:
        return

    try:
        await supabase.insert("mensajes", filas)
    except supabase.SupabaseError:
        logger.exception("No se pudo guardar la transcripción de la sesión %s", sesion_id)


# --------------------------------------------------------------------------- #
# Evaluaciones
# --------------------------------------------------------------------------- #


async def guardar_evaluacion(
    sesion_id: str,
    usuario_id: str | None,
    resultado: dict,
    prediccion_ml: int | None = None,
    respuestas: dict[int, int] | None = None,
) -> None:
    """Guarda el resultado final del test (lo que lee la pantalla Histórico)."""
    fila: dict[str, Any] = {
        "sesion_id": sesion_id,
        "usuario_id": usuario_id,
        "score": resultado["score"],
        "nivel": resultado["nivel"],
        "interpretacion": resultado["interpretacion"],
        "recomendaciones": resultado.get("recomendaciones", []),
        "prediccion_ml": prediccion_ml,
        # jsonb no admite claves int: se serializan como string.
        "respuestas": {str(k): v for k, v in (respuestas or {}).items()},
    }
    await supabase.insert("evaluaciones", fila)


async def listar_evaluaciones(usuario_id: str, limite: int = 50) -> list[dict]:
    """
    Devuelve el histórico del usuario en el contrato que consume la app
    (`HamiltonResult` de `src/types/chat.ts`), más reciente primero.
    """
    filas = await supabase.select(
        "evaluaciones",
        {
            "usuario_id": f"eq.{usuario_id}",
            "select": "sesion_id,score,nivel,interpretacion,recomendaciones,creado_en",
            "order": "creado_en.desc",
            "limit": limite,
        },
    )

    return [
        {
            "sesion_id": fila.get("sesion_id") or "",
            "score": fila.get("score", 0),
            "nivel": fila.get("nivel", ""),
            "interpretacion": fila.get("interpretacion", ""),
            "recomendaciones": fila.get("recomendaciones") or [],
            "fecha": fila.get("creado_en"),
        }
        for fila in filas
    ]
