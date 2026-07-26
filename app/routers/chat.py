"""
Router del endpoint principal de conversación: POST /chat/mensaje.

Lógica interna (ver sección 10 del documento de especificación):
1. Aplica `detectar_riesgo` (filtro determinístico) sobre el mensaje entrante.
2. Corre el loop de tool-use del orquestador (que agrega el mensaje al
   historial de la sesión y ejecuta las tools reales que Claude invoque).
3. Si el filtro detectó riesgo pero el LLM no llamó a
   `activar_protocolo_crisis`, registra una alerta para revisión humana (no
   se fuerza ninguna respuesta automática).
4. Devuelve el texto final del asistente + flags de estado.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.chat.orchestrator import procesar_mensaje
from app.chat.session_store import nuevo_sesion_id
from app.config import settings
from app.db import repositorio
from app.deps import Usuario, usuario_opcional
from app.ml.modelo import ModeloNoDisponibleError
from app.safety.risk_filter import detectar_riesgo, registrar_alerta_discrepancia

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


class MensajeRequest(BaseModel):
    sesion_id: str | None = Field(default=None, description="UUID de sesión; se crea uno si falta")
    mensaje: str = Field(..., min_length=1, description="Texto del usuario")
    lat: float | None = Field(default=None, description="Latitud del usuario, si la compartió")
    lon: float | None = Field(default=None, description="Longitud del usuario, si la compartió")


class ResultadoResponse(BaseModel):
    """Resultado final del test, en el contrato que consume la app móvil."""

    score: int
    nivel: str
    interpretacion: str
    recomendaciones: list[str]
    fecha: str
    sesion_id: str


class MensajeResponse(BaseModel):
    sesion_id: str
    respuesta: str
    protocolo_crisis_activo: bool
    test_completo: bool
    # Campo adicional (no rompe el contrato mínimo de la sección 10): le indica
    # al cliente móvil que debe pedir permiso de ubicación al usuario, tal
    # como dispara la tool `solicitar_ubicacion`.
    ubicacion_solicitada: bool = False
    # Sólo viene en el turno en que el modelo cierra el test.
    resultado: ResultadoResponse | None = None


@router.post("/mensaje", response_model=MensajeResponse)
async def enviar_mensaje(
    payload: MensajeRequest,
    usuario: Usuario | None = Depends(usuario_opcional),
) -> MensajeResponse:
    sesion_id = payload.sesion_id or nuevo_sesion_id()
    usuario_id = usuario.id if usuario else None

    riesgo_detectado_por_filtro = detectar_riesgo(payload.mensaje)

    try:
        resultado = await procesar_mensaje(
            sesion_id=sesion_id,
            mensaje_usuario=payload.mensaje,
            lat=payload.lat,
            lon=payload.lon,
            usuario_id=usuario_id,
        )
    except ModeloNoDisponibleError as exc:
        logger.exception("El modelo ML no está disponible")
        raise HTTPException(
            status_code=503,
            detail="El servicio de evaluación no está disponible en este momento. Intenta más tarde.",
        ) from exc
    except Exception as exc:  # noqa: BLE001 - nunca dejar el chat "colgado"
        logger.exception("Error inesperado procesando el mensaje de la sesión %s", sesion_id)
        raise HTTPException(
            status_code=500,
            detail="Ocurrió un error inesperado procesando tu mensaje.",
        ) from exc

    # Red de seguridad: si el filtro determinístico vio señales de riesgo pero
    # el LLM no activó el protocolo de crisis, se registra para revisión
    # humana. Nunca se reemplaza la respuesta del asistente por esto.
    registrar_alerta_discrepancia(
        sesion_id=sesion_id,
        mensaje_detectado=riesgo_detectado_por_filtro,
        llm_activo_protocolo=resultado["protocolo_crisis_activo"],
    )

    if settings.supabase_habilitado:
        await _persistir_turno(sesion_id, usuario_id, payload.mensaje, resultado)

    return MensajeResponse(
        sesion_id=sesion_id,
        respuesta=resultado["respuesta"],
        protocolo_crisis_activo=resultado["protocolo_crisis_activo"],
        test_completo=resultado["test_completo"],
        ubicacion_solicitada=resultado["ubicacion_solicitada"],
        resultado=resultado.get("resultado"),
    )


async def _persistir_turno(
    sesion_id: str,
    usuario_id: str | None,
    mensaje_usuario: str,
    resultado: dict,
) -> None:
    """
    Guarda la transcripción del turno y, si el test se cerró, la evaluación.

    Nada de esto debe tumbar la conversación: la transcripción es best-effort
    dentro del repositorio y la evaluación se registra pero no se propaga.
    """
    await repositorio.guardar_mensajes(
        sesion_id,
        usuario_id,
        [("user", mensaje_usuario), ("assistant", resultado["respuesta"])],
    )

    evaluacion = resultado.get("resultado")
    if not evaluacion:
        return

    try:
        await repositorio.guardar_evaluacion(
            sesion_id=sesion_id,
            usuario_id=usuario_id,
            resultado=evaluacion,
            prediccion_ml=(resultado.get("resultado_ml") or {}).get("prediccion_ml"),
            respuestas=resultado.get("respuestas"),
        )
    except Exception:  # noqa: BLE001 - el usuario ya tiene su resultado en pantalla
        logger.exception("No se pudo guardar la evaluación de la sesión %s", sesion_id)
