"""
Almacenamiento del estado de sesión.

Dos implementaciones intercambiables detrás de la misma interfaz:

- `MemorySessionStore`: dict en memoria del proceso. Sólo desarrollo — se pierde
  al reiniciar y no sirve con varios workers.
- `SupabaseSessionStore`: persiste el estado en la tabla `public.sesiones`
  (columna `estado`, jsonb). Es la que se usa cuando hay credenciales de
  Supabase en el entorno.

La interfaz es asíncrona porque la implementación real hace I/O de red; el
orquestador ya corre en contexto `async`, así que no bloquea el event loop.

No se guarda en el historial de sesión más información sensible de la
estrictamente necesaria — ver notas en `app/routers/chat.py` sobre logging.
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from typing import Any

from app.config import settings
from app.db import supabase

logger = logging.getLogger(__name__)


class SessionStore(ABC):
    """Contrato que debe cumplir cualquier backend de sesiones."""

    @abstractmethod
    async def get(self, sesion_id: str, default: Any = None) -> Any: ...

    @abstractmethod
    async def set(self, sesion_id: str, value: Any, usuario_id: str | None = None) -> None: ...

    @abstractmethod
    async def exists(self, sesion_id: str) -> bool: ...


class MemorySessionStore(SessionStore):
    """Backend de sesiones en memoria del proceso — solo para desarrollo."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def get(self, sesion_id: str, default: Any = None) -> Any:
        return self._data.get(sesion_id, default)

    async def set(self, sesion_id: str, value: Any, usuario_id: str | None = None) -> None:
        self._data[sesion_id] = value

    async def exists(self, sesion_id: str) -> bool:
        return sesion_id in self._data


class SupabaseSessionStore(SessionStore):
    """
    Estado de sesión persistido en `public.sesiones`.

    Se hace upsert por `id` (el `sesion_id` que genera el router): el primer
    mensaje crea la fila y los siguientes la actualizan.
    """

    TABLA = "sesiones"

    async def get(self, sesion_id: str, default: Any = None) -> Any:
        filas = await supabase.select(
            self.TABLA, {"id": f"eq.{sesion_id}", "select": "estado", "limit": 1}
        )
        if not filas:
            return default
        return filas[0].get("estado") or default

    async def set(self, sesion_id: str, value: Any, usuario_id: str | None = None) -> None:
        fila: dict[str, Any] = {
            "id": sesion_id,
            "estado": value,
            "protocolo_crisis_activo": bool(value.get("protocolo_crisis_activo", False)),
            "test_completo": bool(value.get("test_completo", False)),
        }
        if usuario_id:
            fila["usuario_id"] = usuario_id
        await supabase.insert(self.TABLA, fila, upsert=True)

    async def exists(self, sesion_id: str) -> bool:
        filas = await supabase.select(
            self.TABLA, {"id": f"eq.{sesion_id}", "select": "id", "limit": 1}
        )
        return bool(filas)


def _crear_store() -> SessionStore:
    if settings.supabase_habilitado:
        logger.info("Sesiones: usando Supabase (tabla public.sesiones)")
        return SupabaseSessionStore()
    logger.warning(
        "Sesiones: usando almacenamiento EN MEMORIA. "
        "Configura SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY para persistir."
    )
    return MemorySessionStore()


def nuevo_sesion_id() -> str:
    return str(uuid.uuid4())


# Instancia única compartida por la app durante su ciclo de vida.
session_store: SessionStore = _crear_store()
