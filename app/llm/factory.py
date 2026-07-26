"""
Selección del proveedor de texto según el `.env`.
Soporta cadena de respaldo: si el primario falla, prueba el fallback, luego el terciario.

Cambiar de IA es cambiar `LLM_PROVIDER`. Los presets rellenan `base_url` y un
modelo por defecto; cualquiera de los dos puede sobreescribirse con
`LLM_BASE_URL` y `LLM_MODEL`.

Para una API compatible con OpenAI que no esté en la lista, usa
`LLM_PROVIDER=openai_compat` y define `LLM_BASE_URL` + `LLM_MODEL` a mano.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import settings
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMError, ProveedorLLM
from app.llm.fallback_provider import FallbackProvider
from app.llm.openai_compat import OpenAICompatProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Preset:
    base_url: str
    modelo: str


# Modelos por defecto elegidos por soportar tool-calling, que es imprescindible:
# el asistente registra cada respuesta del test y dispara el cálculo del SVM.
PRESETS: dict[str, Preset] = {
    "deepseek": Preset("https://api.deepseek.com/v1", "deepseek-v4-flash"),
    "nvidia": Preset("https://integrate.api.nvidia.com/v1", "meta/llama-3.3-70b-instruct"),
    "openai": Preset("https://api.openai.com/v1", "gpt-4o-mini"),
    "groq": Preset("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "openrouter": Preset("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct"),
    "ollama": Preset("http://localhost:11434/v1", "llama3.1"),
    # Google expone una capa compatible con OpenAI, así que no hace falta SDK.
    "gemini": Preset("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.5-flash"),
    "openai_compat": Preset("", ""),
}

_proveedor: ProveedorLLM | None = None


def _crear_un_proveedor(
    provider_name: str | None,
    api_key: str,
    base_url: str,
    model: str,
) -> ProveedorLLM | None:
    """Crea un solo proveedor si hay api_key. Devuelve None si no."""
    if not api_key:
        return None

    nombre = (provider_name or settings.llm_provider).strip().lower()

    if nombre == "anthropic":
        return AnthropicProvider(api_key=api_key, modelo=model or settings.anthropic_model)

    preset = PRESETS.get(nombre)
    if preset is None:
        logger.warning("Proveedor '%s' no reconocido en cadena de respaldo, ignorado.", nombre)
        return None

    url = base_url or preset.base_url
    if not url:
        logger.warning("Falta URL base para '%s' en cadena de respaldo, ignorado.", nombre)
        return None

    modelo_final = model or preset.modelo
    if not modelo_final:
        logger.warning("Falta modelo para '%s' en cadena de respaldo, ignorado.", nombre)
        return None

    return OpenAICompatProvider(nombre=nombre, base_url=url, api_key=api_key, modelo=modelo_final)


def crear_proveedor() -> ProveedorLLM:
    """Instancia el primario + fallbacks opcionales. Lanza `LLMError` si no hay ninguno."""
    primario = _crear_un_proveedor(
        provider_name=None,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
    )
    if primario is None:
        raise LLMError(
            "No hay proveedor de texto configurado. Define LLM_API_KEY en el .env."
        )

    # Reunir respaldos (ignorando los que no tengan key).
    respaldos: list[ProveedorLLM] = [primario]

    for nivel in ("fallback", "tertiary"):
        key = getattr(settings, f"llm_{nivel}_api_key", "")
        if not key:
            continue
        prov = _crear_un_proveedor(
            # Cada respaldo puede ser de otro proveedor (p. ej. deepseek -> gemini).
            provider_name=getattr(settings, f"llm_{nivel}_provider", "") or None,
            api_key=key,
            base_url=getattr(settings, f"llm_{nivel}_base_url", ""),
            model=getattr(settings, f"llm_{nivel}_model", ""),
        )
        if prov is not None:
            respaldos.append(prov)

    if len(respaldos) == 1:
        return primario

    logger.info(
        "Cadena de respaldo activa: %s",
        " → ".join(f"{p.nombre}/{p.modelo}" for p in respaldos),
    )
    return FallbackProvider(respaldos)


def get_proveedor() -> ProveedorLLM:
    """Proveedor compartido (se crea en la primera petición y se reutiliza)."""
    global _proveedor
    if _proveedor is None:
        _proveedor = crear_proveedor()
        logger.info("Proveedor de texto: %s (modelo %s)", _proveedor.nombre, _proveedor.modelo)
    return _proveedor


async def cerrar_proveedor() -> None:
    global _proveedor
    if _proveedor is not None:
        await _proveedor.cerrar()
        _proveedor = None
