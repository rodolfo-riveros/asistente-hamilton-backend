"""
Adaptador para cualquier API compatible con OpenAI (`/chat/completions`).

Cubre DeepSeek, NVIDIA NIM, OpenAI, Groq, Together, OpenRouter, Ollama y
cualquier servidor que hable ese dialecto: sólo cambian `base_url` y `model`.

Se usa httpx directamente en vez del SDK de OpenAI para no añadir una
dependencia más y porque la petición es una sola llamada JSON.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.llm.base import LlamadaTool, LLMError, ProveedorLLM, RespuestaLLM

logger = logging.getLogger(__name__)


def _tools_a_openai(tools: list[dict]) -> list[dict]:
    """Convierte el esquema neutro de `app/chat/tools.py` al formato OpenAI."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
    ]


def _historial_a_openai(system: str, mensajes: list[dict]) -> list[dict]:
    salida: list[dict] = [{"role": "system", "content": system}]

    for mensaje in mensajes:
        rol = mensaje.get("role")

        if rol == "tool":
            salida.append(
                {
                    "role": "tool",
                    "tool_call_id": mensaje.get("tool_call_id", ""),
                    "content": mensaje.get("content", ""),
                }
            )
            continue

        if rol == "assistant":
            turno: dict[str, Any] = {"role": "assistant", "content": mensaje.get("content") or ""}
            llamadas = mensaje.get("tool_calls") or []
            if llamadas:
                turno["tool_calls"] = [
                    {
                        "id": llamada["id"],
                        "type": "function",
                        "function": {
                            "name": llamada["nombre"],
                            "arguments": json.dumps(
                                llamada.get("argumentos") or {}, ensure_ascii=False
                            ),
                        },
                    }
                    for llamada in llamadas
                ]
            salida.append(turno)
            continue

        salida.append({"role": "user", "content": mensaje.get("content", "")})

    return salida


class OpenAICompatProvider(ProveedorLLM):
    def __init__(
        self,
        *,
        nombre: str,
        base_url: str,
        api_key: str,
        modelo: str,
        timeout: float = 90.0,
    ) -> None:
        if not base_url:
            raise LLMError(f"Falta la URL base del proveedor '{nombre}' (LLM_BASE_URL).")
        if not api_key:
            raise LLMError(f"Falta la clave del proveedor '{nombre}' (LLM_API_KEY).")

        self.nombre = nombre
        self.modelo = modelo
        self._cliente = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def generar(
        self,
        *,
        system: str,
        mensajes: list[dict],
        tools: list[dict],
        max_tokens: int,
    ) -> RespuestaLLM:
        payload = {
            "model": self.modelo,
            "messages": _historial_a_openai(system, mensajes),
            "tools": _tools_a_openai(tools),
            "tool_choice": "auto",
            "max_tokens": max_tokens,
            "temperature": 0.6,
        }

        try:
            respuesta = await self._cliente.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"No se pudo contactar con {self.nombre}: {exc}") from exc

        if not respuesta.is_success:
            raise LLMError(
                f"{self.nombre} respondió {respuesta.status_code}: {respuesta.text[:300]}"
            )

        try:
            cuerpo = respuesta.json()
            mensaje = cuerpo["choices"][0]["message"]
        except (ValueError, KeyError, IndexError) as exc:
            raise LLMError(f"Respuesta inesperada de {self.nombre}: {exc}") from exc

        return RespuestaLLM(
            texto=(mensaje.get("content") or "").strip(),
            tool_calls=self._extraer_tool_calls(mensaje),
        )

    def _extraer_tool_calls(self, mensaje: dict) -> list[LlamadaTool]:
        llamadas = []
        for i, llamada in enumerate(mensaje.get("tool_calls") or []):
            funcion = llamada.get("function") or {}
            crudos = funcion.get("arguments") or "{}"
            try:
                # Algunos proveedores mandan los argumentos ya como objeto.
                argumentos = crudos if isinstance(crudos, dict) else json.loads(crudos)
            except json.JSONDecodeError:
                logger.warning(
                    "%s devolvió argumentos no parseables para %s: %r",
                    self.nombre,
                    funcion.get("name"),
                    crudos,
                )
                argumentos = {}

            llamadas.append(
                LlamadaTool(
                    id=llamada.get("id") or f"call_{i}",
                    nombre=funcion.get("name", ""),
                    argumentos=argumentos,
                )
            )
        return llamadas

    async def cerrar(self) -> None:
        await self._cliente.aclose()
