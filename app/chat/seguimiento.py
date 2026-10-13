"""
Modo seguimiento / recuperación (v5).

El estado de la persona se calcula AQUÍ, en el backend, a partir de sus
evaluaciones guardadas; el modelo sólo recibe el bloque ya decidido en el
system prompt del turno. Así la decisión es determinista, testeable y no
depende de que el LLM "interprete" un histórico.

No cambia nada clínico: los 17 ítems, sus escalas, el SVM y el protocolo de
crisis son los mismos. Sólo cambia si Aurora toma la iniciativa de seguir
con el test o se dedica a acompañar.

Umbrales (documentados también en la nota "Chat v5 y build" de la app):

- MODO SEGUIMIENTO si se cumple cualquiera:
  · el último nivel es "Sin depresión", o
  · la tendencia (última vs. anterior) MEJORA CLARAMENTE y el último nivel no
    es "Depresión severa".
- MEJORA CLARA = baja al menos un nivel (orden de `ORDEN_NIVEL`) o el puntaje
  HDRS-17 baja ≥ `PUNTOS_CAMBIO_CLARO` (6 de 52). EMPEORA = lo simétrico.
  Lo demás es "estable". Con una sola evaluación no hay tendencia.
- En cualquier otro caso (moderada/severa sin mejora clara, o sin
  evaluaciones) el test sigue su ritmo normal (PARTE 1 del prompt).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.db import repositorio

logger = logging.getLogger(__name__)

# Gravedad relativa de los niveles del contrato (`app/chat/resultado.py`).
ORDEN_NIVEL: dict[str, int] = {
    "Sin depresión": 0,
    "Depresión leve": 1,
    "Depresión moderada": 2,
    "Depresión severa": 3,
}

# Cambio de puntaje HDRS-17 (0–52) que consideramos "claro" entre dos
# evaluaciones. 6 puntos ≈ la anchura de una banda de severidad de Hamilton.
PUNTOS_CAMBIO_CLARO = 6

# Evaluaciones que se leen para calcular el estado (sólo hacen falta 2).
EVALUACIONES_A_LEER = 5

NIVEL_SIN_DEPRESION = "Sin depresión"
NIVEL_SEVERO = "Depresión severa"

_LINEA = "═══════════════════════════════════════════"


def _a_datetime(valor: Any) -> datetime | None:
    if isinstance(valor, datetime):
        fecha = valor
    elif isinstance(valor, str) and valor:
        try:
            fecha = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=timezone.utc)


def _tendencia(ultima: dict, anterior: dict) -> str:
    """'mejora' | 'empeora' | 'estable' (última frente a la anterior)."""
    n_ult = ORDEN_NIVEL.get(str(ultima.get("nivel")))
    n_ant = ORDEN_NIVEL.get(str(anterior.get("nivel")))
    try:
        delta = int(ultima.get("score")) - int(anterior.get("score"))
    except (TypeError, ValueError):
        delta = 0

    if (n_ult is not None and n_ant is not None and n_ult < n_ant) or delta <= -PUNTOS_CAMBIO_CLARO:
        return "mejora"
    if (n_ult is not None and n_ant is not None and n_ult > n_ant) or delta >= PUNTOS_CAMBIO_CLARO:
        return "empeora"
    return "estable"


def calcular_estado(evaluaciones: list[dict], ahora: datetime | None = None) -> dict:
    """
    Estado de la persona a partir de sus evaluaciones (más reciente primero,
    como las devuelve `repositorio.listar_evaluaciones`).

    Devuelve `{modo, ultimo_nivel, ultimo_score, dias_desde_ultima, tendencia,
    n_evaluaciones}` con `modo` ∈ {'sin_evaluaciones', 'seguimiento', 'evaluacion'}.
    """
    evaluaciones = [e for e in evaluaciones or [] if isinstance(e, dict) and e.get("nivel")]
    if not evaluaciones:
        return {
            "modo": "sin_evaluaciones",
            "ultimo_nivel": None,
            "ultimo_score": None,
            "dias_desde_ultima": None,
            "tendencia": None,
            "n_evaluaciones": 0,
        }

    ahora = ahora or datetime.now(timezone.utc)
    ultima = evaluaciones[0]
    nivel = str(ultima["nivel"])
    fecha = _a_datetime(ultima.get("fecha") or ultima.get("creado_en"))
    dias = max(0, (ahora - fecha).days) if fecha else None
    tendencia = _tendencia(ultima, evaluaciones[1]) if len(evaluaciones) >= 2 else None

    seguimiento = nivel == NIVEL_SIN_DEPRESION or (
        tendencia == "mejora" and nivel != NIVEL_SEVERO
    )
    return {
        "modo": "seguimiento" if seguimiento else "evaluacion",
        "ultimo_nivel": nivel,
        "ultimo_score": ultima.get("score"),
        "dias_desde_ultima": dias,
        "tendencia": tendencia,
        "n_evaluaciones": len(evaluaciones),
    }


def _hace(dias: int | None) -> str:
    if dias is None:
        return "fecha desconocida"
    if dias == 0:
        return "hoy"
    if dias == 1:
        return "hace 1 día"
    return f"hace {dias} días"


def bloque_estado(estado: dict, items_registrados: int = 0) -> str:
    """Bloque para el system prompt del turno. `items_registrados` = test a medias."""
    cabecera = f"\n\n{_LINEA}\nESTADO DE LA PERSONA (calculado por el sistema; no se lo recites)\n{_LINEA}\n"
    modo = estado.get("modo")

    if modo == "sin_evaluaciones":
        return cabecera + (
            "Todavía no ha completado ninguna evaluación. Sigue el ritmo normal del test "
            "(PARTE 1): muy espaciado y siempre detrás de la conversación."
        )

    resumen = (
        f"Última evaluación: {estado['ultimo_nivel']} ({estado.get('ultimo_score')}/52), "
        f"{_hace(estado.get('dias_desde_ultima'))}."
    )
    if estado.get("tendencia"):
        resumen += f" Tendencia respecto a la anterior: {estado['tendencia']}."

    if modo == "seguimiento":
        texto = (
            resumen + "\nMODO SEGUIMIENTO: la persona está bien o mejorando claramente.\n"
            "- NO inicies ni continúes ítems del test por iniciativa tuya.\n"
            "- Conversa de otros temas: cómo se siente, su día, sus logros (celébralos en "
            "corto), qué le está ayudando, planes y metas, y prevención de recaídas (rutinas "
            "de sueño, movimiento, contacto con su gente, señales a las que estar atenta).\n"
            "- Sólo retoma la evaluación si ELLA lo pide, o si durante varios mensajes aparecen "
            "señales claras de empeoramiento (tristeza persistente, no duerme, se aísla, nada "
            "le interesa); entonces propónselo con mucha suavidad, como opción, nunca de golpe.\n"
            "- El protocolo de crisis (PARTE 2) sigue vigente igual que siempre."
        )
        if items_registrados:
            texto += (
                f"\n- Hay un test a medias ({items_registrados} de 17 ítems): como está bien, "
                "NO insistas en terminarlo ni lo menciones salvo que ella quiera."
            )
        return cabecera + texto

    texto = resumen + "\nSigue el ritmo normal del test (PARTE 1)."
    if estado.get("tendencia") == "empeora":
        texto += (
            " La última evaluación salió peor que la anterior: escucha con especial atención y, "
            "si encaja, recuérdale con naturalidad que hablar con un profesional puede ayudar "
            "(puedes ofrecer `buscar_centros_cercanos`)."
        )
    return cabecera + texto


async def contexto_seguimiento(usuario_id: str | None, items_registrados: int = 0) -> str:
    """
    Lee las evaluaciones y arma el bloque. Best-effort: sin usuario o si la base
    de datos falla, no se añade nada y Aurora sigue con su comportamiento normal.
    """
    if not usuario_id:
        return ""
    try:
        evaluaciones = await repositorio.listar_evaluaciones(usuario_id, limite=EVALUACIONES_A_LEER)
    except Exception:  # noqa: BLE001 - nunca tumbar el chat por esto
        logger.warning("No se pudo calcular el estado de seguimiento de %s", usuario_id)
        return ""
    return bloque_estado(calcular_estado(evaluaciones), items_registrados)
