"""
Contrato común para cualquier proveedor de generación de texto.

El orquestador guarda el historial en un formato NEUTRO (ni Anthropic ni
OpenAI) y cada adaptador lo traduce al dialecto de su API. Así se puede
cambiar de proveedor con una variable de entorno sin tocar la lógica del test
de Hamilton ni el estado de las sesiones ya guardadas.

Formato neutro del historial (lista de dicts JSON-serializables):

    {"role": "user",      "content": "texto del usuario"}
    {"role": "assistant", "content": "texto", "tool_calls": [ ... ]}
    {"role": "tool",      "tool_call_id": "id", "name": "tool", "content": "json"}

donde cada elemento de `tool_calls` es:

    {"id": "...", "nombre": "registrar_respuesta", "argumentos": {...}}
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class LLMError(RuntimeError):
    """Fallo al hablar con el proveedor (red, credenciales, cuota, formato)."""


@dataclass
class LlamadaTool:
    """Petición del modelo para ejecutar una tool del backend."""

    id: str
    nombre: str
    argumentos: dict


@dataclass
class RespuestaLLM:
    """Turno del asistente, ya normalizado."""

    texto: str = ""
    tool_calls: list[LlamadaTool] = field(default_factory=list)

    @property
    def pide_tools(self) -> bool:
        return bool(self.tool_calls)

    def a_mensaje(self) -> dict:
        """Representación de este turno para guardar en el historial neutro."""
        mensaje: dict = {"role": "assistant", "content": self.texto}
        if self.tool_calls:
            mensaje["tool_calls"] = [
                {"id": t.id, "nombre": t.nombre, "argumentos": t.argumentos}
                for t in self.tool_calls
            ]
        return mensaje


class ProveedorLLM(ABC):
    """Cualquier API de generación de texto con soporte de tool-calling."""

    #: Nombre legible para logs y para el endpoint /salud.
    nombre: str = "desconocido"
    #: Identificador del modelo en uso.
    modelo: str = ""

    @abstractmethod
    async def generar(
        self,
        *,
        system: str,
        mensajes: list[dict],
        tools: list[dict],
        max_tokens: int,
    ) -> RespuestaLLM:
        """Un turno de conversación. Debe lanzar `LLMError` ante cualquier fallo."""

    async def cerrar(self) -> None:
        """Libera conexiones. Se llama en el shutdown de la app."""
