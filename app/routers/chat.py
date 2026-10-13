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

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.chat.orchestrator import procesar_mensaje
from app.chat.session_store import nuevo_sesion_id
from app.config import settings
from app.db import repositorio
from app.deps import Usuario, usuario_actual, usuario_opcional
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
    usuario_id = usuario.id if usuario else None

    # Hilo único por persona: si la app no manda sesion_id (caso normal), se
    # reutiliza el hilo abierto del usuario en vez de empezar una conversación
    # nueva en cada arranque.
    sesion_id = payload.sesion_id
    if sesion_id and settings.supabase_habilitado:
        # Un sesion_id ajeno (o mal formado) se ignora: nadie puede leer ni
        # escribir en el hilo de otra persona mandando su id.
        if not await repositorio.sesion_utilizable(sesion_id, usuario_id):
            logger.info("sesion_id ignorado: no pertenece al usuario o no es válido")
            sesion_id = None
    if not sesion_id and usuario_id and settings.supabase_habilitado:
        sesion_id = await repositorio.sesion_activa(usuario_id)
    sesion_id = sesion_id or nuevo_sesion_id()

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


# --------------------------------------------------------------------------- #
# Continuidad: recuperar conversaciones anteriores
# --------------------------------------------------------------------------- #


class MensajeGuardado(BaseModel):
    rol: str
    contenido: str
    creado_en: str


class SesionResumen(BaseModel):
    sesion_id: str
    test_completo: bool
    protocolo_crisis_activo: bool
    creado_en: str
    actualizado_en: str


class SesionDetalle(SesionResumen):
    mensajes: list[MensajeGuardado] = []


def _a_resumen(fila: dict) -> SesionResumen:
    return SesionResumen(
        sesion_id=fila["id"],
        test_completo=bool(fila.get("test_completo")),
        protocolo_crisis_activo=bool(fila.get("protocolo_crisis_activo")),
        creado_en=fila.get("creado_en") or "",
        actualizado_en=fila.get("actualizado_en") or "",
    )


def _exigir_supabase() -> None:
    if not settings.supabase_habilitado:
        raise HTTPException(
            status_code=503,
            detail="El historial no está disponible: Supabase no está configurado.",
        )


@router.get("/sesiones", response_model=list[SesionResumen])
async def listar_sesiones(usuario: Usuario = Depends(usuario_actual)) -> list[SesionResumen]:
    """Conversaciones del usuario, la más reciente primero."""
    _exigir_supabase()
    filas = await repositorio.listar_sesiones(usuario.id)
    return [_a_resumen(fila) for fila in filas]


@router.get("/sesiones/ultima", response_model=SesionDetalle | None)
async def ultima_sesion(usuario: Usuario = Depends(usuario_actual)) -> SesionDetalle | None:
    """
    Última conversación con sus mensajes: es lo que la app carga al abrirse para
    que el usuario siga donde lo dejó en vez de empezar de cero.
    """
    _exigir_supabase()
    filas = await repositorio.listar_sesiones(usuario.id, limite=1)
    if not filas:
        return None
    return await _detalle(filas[0], usuario.id)


@router.get("/sesiones/{sesion_id}", response_model=SesionDetalle)
async def detalle_sesion(
    sesion_id: str, usuario: Usuario = Depends(usuario_actual)
) -> SesionDetalle:
    _exigir_supabase()
    fila = await repositorio.obtener_sesion(sesion_id, usuario.id)
    if fila is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    return await _detalle(fila, usuario.id)


async def _detalle(fila: dict, usuario_id: str) -> SesionDetalle:
    mensajes = await repositorio.obtener_mensajes(fila["id"], usuario_id)
    resumen = _a_resumen(fila)
    return SesionDetalle(
        **resumen.model_dump(),
        mensajes=[
            MensajeGuardado(
                rol=m.get("rol", "assistant"),
                contenido=m.get("contenido", ""),
                creado_en=m.get("creado_en") or "",
            )
            for m in mensajes
        ],
    )


@router.get("/historial", response_model=list[MensajeGuardado])
async def historial(
    usuario: Usuario = Depends(usuario_actual),
    limite: int = Query(default=200, ge=1, le=500),
) -> list[MensajeGuardado]:
    """
    Todo el hilo del usuario, en orden cronológico.

    Es lo que la app pinta al abrirse: una sola conversación continua, como en
    una app de mensajería, sin que el usuario tenga que elegir "sesiones".
    """
    _exigir_supabase()
    filas = await repositorio.historial_usuario(usuario.id, limite=limite)
    return [
        MensajeGuardado(
            rol=f.get("rol", "assistant"),
            contenido=f.get("contenido", ""),
            creado_en=f.get("creado_en") or "",
        )
        for f in filas
    ]
