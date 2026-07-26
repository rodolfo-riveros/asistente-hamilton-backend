from __future__ import annotations

import logging

from app.llm.base import LLMError, ProveedorLLM, RespuestaLLM

logger = logging.getLogger(__name__)


class FallbackProvider(ProveedorLLM):
    """Envuelve N proveedores y los prueba en orden hasta que uno responda."""

    def __init__(self, proveedores: list[ProveedorLLM]) -> None:
        if not proveedores:
            raise LLMError("FallbackProvider necesita al menos un proveedor.")
        self._proveedores = proveedores
        self.nombre = " | ".join(p.nombre for p in proveedores)
        self.modelo = proveedores[0].modelo

    async def generar(
        self,
        *,
        system: str,
        mensajes: list[dict],
        tools: list[dict],
        max_tokens: int,
    ) -> RespuestaLLM:
        ultimo_error: str | None = None
        for i, p in enumerate(self._proveedores):
            try:
                return await p.generar(
                    system=system,
                    mensajes=mensajes,
                    tools=tools,
                    max_tokens=max_tokens,
                )
            except LLMError as e:
                logger.warning(
                    "Fallback: proveedor %d (%s) falló: %s",
                    i + 1, p.nombre, e,
                )
                ultimo_error = f"{p.nombre}: {e}"

        raise LLMError(
            f"Todos los proveedores de respaldo fallaron. Último error: {ultimo_error}"
        )

    async def cerrar(self) -> None:
        for p in self._proveedores:
            await p.cerrar()
