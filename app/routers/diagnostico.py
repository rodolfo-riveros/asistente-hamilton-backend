"""
Router de la ficha de diagnóstico — sección 2 del `CONTRATO_V3.md`.

`GET /diagnostico` reúne en un solo documento lo que la persona puede llevarle a
un profesional: sus datos, la última evaluación del test, el histórico, los 17
ítems de la escala (con los que aún faltan por preguntar), cuánto ha usado la
app y hacia dónde va su puntaje.

No calcula nada clínico nuevo: el nivel siempre viene del SVM ya guardado en
`evaluaciones`. La `tendencia` es una lectura descriptiva de los dos últimos
puntajes, no un pronóstico.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.chat.items import TOTAL_ITEMS, ficha_items, normalizar_respuestas
from app.config import settings
from app.db import repositorio, supabase
from app.deps import Usuario, usuario_actual

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/diagnostico", tags=["diagnostico"])

# Diferencia mínima de puntaje para hablar de cambio y no de ruido.
UMBRAL_TENDENCIA = 3

AVISO = (
    "Este documento es una herramienta de orientación generada por una app de "
    "acompañamiento; no constituye un diagnóstico médico ni reemplaza la "
    "evaluación de un profesional de salud mental."
)


class PerfilFicha(BaseModel):
    nombre: str = ""
    edad: int | None = None
    ciudad: str | None = None
    ocupacion: str | None = None


class EvaluacionFicha(BaseModel):
    score: int
    nivel: str
    interpretacion: str
    recomendaciones: list[str] = []
    fecha: str | None = None
    sesion_id: str | None = None


class EvaluacionResumen(BaseModel):
    fecha: str | None = None
    score: int
    nivel: str


class ItemFicha(BaseModel):
    item: int
    etiqueta: str
    valor: int | None = None
    maximo: int
    registrado: bool = False


class ProgresoFicha(BaseModel):
    registrados: int
    total: int
    porcentaje: int


class ActividadFicha(BaseModel):
    conversaciones: int = 0
    mensajes: int = 0
    primera_conversacion: str | None = None
    ultima_conversacion: str | None = None
    actividades_creadas: int = 0
    actividades_completadas: int = 0
    tasa_cumplimiento: int = 0


class DiagnosticoResponse(BaseModel):
    generado_en: str
    perfil: PerfilFicha
    ultima_evaluacion: EvaluacionFicha | None = None
    evaluaciones: list[EvaluacionResumen] = []
    items: list[ItemFicha] = []
    progreso: ProgresoFicha
    actividad: ActividadFicha
    tendencia: str | None = None
    aviso: str = AVISO


def calcular_tendencia(scores: list[int]) -> str | None:
    """
    Compara los dos puntajes más recientes. `scores` viene de más reciente a más
    antiguo, igual que el histórico de `GET /evaluaciones`.

    En la escala de Hamilton un puntaje MÁS BAJO son menos síntomas, así que
    bajar 3 o más puntos es "mejorando" y subir 3 o más es "empeorando". Con
    menos de dos evaluaciones no hay nada que comparar: `None`.
    """
    if len(scores) < 2:
        return None

    actual, anterior = scores[0], scores[1]
    diferencia = actual - anterior

    if diferencia <= -UMBRAL_TENDENCIA:
        return "mejorando"
    if diferencia >= UMBRAL_TENDENCIA:
        return "empeorando"
    return "estable"


@router.get("", response_model=DiagnosticoResponse)
async def ficha(usuario: Usuario = Depends(usuario_actual)) -> DiagnosticoResponse:
    if not settings.supabase_habilitado:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La ficha de diagnóstico necesita Supabase configurado.",
        )

    try:
        perfil = await repositorio.perfil_completo(usuario.id) or {}
        evaluaciones = await repositorio.listar_evaluaciones(usuario.id)
        respuestas = normalizar_respuestas(await repositorio.respuestas_ultima_sesion(usuario.id))
        estadisticas = await repositorio.estadisticas_usuario(usuario.id)
    except supabase.SupabaseError as exc:
        logger.exception("No se pudo armar la ficha de diagnóstico de %s", usuario.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo generar tu ficha en este momento.",
        ) from exc

    items = ficha_items(respuestas)
    registrados = sum(1 for i in items if i["registrado"])

    return DiagnosticoResponse(
        generado_en=datetime.now(timezone.utc).isoformat(),
        perfil=PerfilFicha(
            nombre=perfil.get("nombre") or usuario.nombre,
            edad=perfil.get("edad"),
            ciudad=perfil.get("ciudad"),
            ocupacion=perfil.get("ocupacion"),
        ),
        ultima_evaluacion=EvaluacionFicha(**evaluaciones[0]) if evaluaciones else None,
        evaluaciones=[
            EvaluacionResumen(fecha=e.get("fecha"), score=e["score"], nivel=e["nivel"])
            for e in evaluaciones
        ],
        items=[ItemFicha(**i) for i in items],
        progreso=ProgresoFicha(
            registrados=registrados,
            total=TOTAL_ITEMS,
            porcentaje=round(registrados * 100 / TOTAL_ITEMS),
        ),
        actividad=ActividadFicha(**estadisticas),
        tendencia=calcular_tendencia([e["score"] for e in evaluaciones]),
    )
