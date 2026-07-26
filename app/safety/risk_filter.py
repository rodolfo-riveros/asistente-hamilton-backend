"""
Filtro de seguridad determinístico — red adicional, independiente del LLM.

Este filtro NO reemplaza el criterio conversacional del asistente (que tiene
mucho más contexto y es la capa principal de detección, ver
`app/prompts/system_prompt.py`, Parte 2). Se ejecuta sobre cada mensaje
entrante del usuario, antes o en paralelo a la llamada a Claude.

Si el filtro detecta riesgo pero el LLM NO llamó a `activar_protocolo_crisis`,
se registra una alerta interna (log/métrica) para revisión humana — nunca se
fuerza una respuesta automática que reemplace al asistente.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone

logger = logging.getLogger("risk_filter")

_PATRONES_RIESGO = [
    r"quitarme la vida", r"quitarse la vida", r"suicid", r"no quiero vivir",
    r"ya no quiero seguir", r"terminar con todo", r"no vale la pena seguir",
    r"quiero morir", r"prefiero estar muerto", r"prefiero estar muerta",
    r"hacerme dano", r"lastimarme", r"cortarme", r"autolesion",
    r"ya no aguanto mas", r"no tengo salida", r"mejor no estar",
]
_PATRONES_RE = re.compile("|".join(_PATRONES_RIESGO))


def _normalizar(texto: str) -> str:
    texto = texto.lower()
    texto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def detectar_riesgo(texto_usuario: str) -> bool:
    """True si el mensaje del usuario matchea algún patrón de riesgo conocido."""
    if not texto_usuario:
        return False
    return bool(_PATRONES_RE.search(_normalizar(texto_usuario)))


def registrar_alerta_discrepancia(sesion_id: str, mensaje_detectado: bool, llm_activo_protocolo: bool) -> None:
    """
    Registra una métrica/alerta cuando el filtro determinístico detecta riesgo
    pero el LLM no llamó a `activar_protocolo_crisis` (o viceversa, para
    trazabilidad completa).

    Deliberadamente NO se registra el texto completo del mensaje del usuario —
    solo el evento, para no dejar contenido sensible en logs no cifrados.
    """
    if mensaje_detectado and not llm_activo_protocolo:
        logger.warning(
            "ALERTA revisión humana: filtro determinístico detectó riesgo pero el LLM "
            "no activó el protocolo de crisis. sesion_id=%s timestamp=%s",
            sesion_id,
            datetime.now(timezone.utc).isoformat(),
        )
    elif mensaje_detectado and llm_activo_protocolo:
        logger.info(
            "Filtro determinístico y LLM coinciden en riesgo detectado. "
            "sesion_id=%s timestamp=%s",
            sesion_id,
            datetime.now(timezone.utc).isoformat(),
        )
