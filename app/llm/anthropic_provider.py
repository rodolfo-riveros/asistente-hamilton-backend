"""
Adaptador para la API de Anthropic (Claude).

Usa el cliente asíncrono del SDK: la versión síncrona bloquearía el event loop
de FastAPI durante toda la generación.

Particularidad de Anthropic frente a OpenAI: los resultados de tools no son
mensajes con `role: "tool"`, sino bloques `tool_result` dentro de un mensaje
`role: "user"`. Por eso el conversor agrupa los resultados consecutivos.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from app.llm.base import LlamadaTool, LLMError, ProveedorLLM, RespuestaLLM

logger = logging.getLogger(__name__)


def _historial_a_anthropic(mensajes: list[dict]) -> list[dict]:
    salida: list[dict] = []
    resultados_pendientes: list[dict] = []

    def volcar_pendientes() -> None:
        if resultados_pendientes:
            salida.append({"role": "user", "content": list(resultados_pendientes)})
            resultados_pendientes.clear()

    for mensaje in mensajes:
        rol = mensaje.get("role")

        if rol == "tool":
            resultados_pendientes.append(
                {
                    "type": "tool_result",
                    "tool_use_id": mensaje.get("tool_call_id", ""),
                    "content": mensaje.get("content", ""),
                }
            )
            continue

        volcar_pendientes()

        if rol == "assistant":
            bloques: list[dict[str, Any]] = []
            texto = mensaje.get("content") or ""
            if texto:
                bloques.append({"type": "text", "text": texto})
            for llamada in mensaje.get("tool_calls") or []:
                bloques.append(
                    {
                        "type": "tool_use",
                        "id": llamada["id"],
                        "name": llamada["nombre"],
                        "input": llamada.get("argumentos") or {},
                    }
                )
            # Anthropic rechaza turnos de asistente vacíos.
            salida.append({"role": "assistant", "content": bloques or [{"type": "text", "text": "…"}]})
            continue

        salida.append({"role": "user", "content": mensaje.get("content", "")})

    volcar_pendientes()
    return salida


class AnthropicProvider(ProveedorLLM):
    nombre = "anthropic"

    def __init__(self, *, api_key: str, modelo: str) -> None:
        if not api_key:
            raise LLMError("Falta ANTHROPIC_API_KEY.")
        self.modelo = modelo
        self._cliente = anthropic.AsyncAnthropic(api_key=api_key)

    async def generar(
        self,
        *,
        system: str,
        mensajes: list[dict],
        tools: list[dict],
        max_tokens: int,
    ) -> RespuestaLLM:
        try:
            respuesta = await self._cliente.messages.create(
                model=self.modelo,
                max_tokens=max_tokens,
                system=system,
                messages=_historial_a_anthropic(mensajes),
                tools=tools,
            )
        except anthropic.APIError as exc:
            raise LLMError(f"Error de la API de Anthropic: {exc}") from exc

        textos = [b.text for b in respuesta.content if b.type == "text"]
        llamadas = [
            LlamadaTool(
                id=b.id,
                nombre=b.name,
                argumentos=b.input if isinstance(b.input, dict) else json.loads(b.input or "{}"),
            )
            for b in respuesta.content
            if b.type == "tool_use"
        ]

        return RespuestaLLM(texto="\n".join(textos).strip(), tool_calls=llamadas)

    async def cerrar(self) -> None:
        await self._cliente.close()
