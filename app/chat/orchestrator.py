"""
Orquestador del loop de tool-use, independiente del proveedor de IA.

Implementa el ciclo estándar:
1. Pide un turno al proveedor configurado (`app/llm/`) con el historial.
2. Si la respuesta trae tool calls, ejecuta la función real del backend.
3. Devuelve cada resultado como un mensaje `role: "tool"` en el historial.
4. Repite hasta que el modelo ya no pida más tools.

El historial se guarda en el FORMATO NEUTRO descrito en `app/llm/base.py`, como
dicts planos JSON-serializables: así el mismo estado sirve con Claude, DeepSeek,
NVIDIA o cualquier otro proveedor, y `session_store` puede persistirlo tal cual.
El estado del test (respuestas registradas, si el protocolo de crisis está
activo, si el test ya se completó) se cachea por `sesion_id`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.chat import acciones as acc
from app.chat.companero import enlaces_musica, memoria_de
from app.chat.resultado import construir_resultado
from app.chat.seguimiento import contexto_seguimiento
from app.chat.session_store import session_store
from app.chat.tools import TOOLS
from app.db import repositorio, supabase
from app.llm.base import LLMError
from app.llm.factory import get_proveedor
from app.locations.centros import buscar_centros_cercanos
from app.ml.modelo import N_ITEMS, ModeloNoDisponibleError, calcular_resultado
from app.prompts.system_prompt import SYSTEM_PROMPT
from app.safety.recursos import RECURSOS_NACIONALES_PE

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 8  # límite de seguridad para no entrar en loops infinitos
MAX_TOKENS = 2048
# Mensajes recientes que viajan al modelo en cada turno del hilo continuo.
MAX_MENSAJES_CONTEXTO = 40

MENSAJE_ERROR_API = (
    "Uy, tuve un problema técnico justo ahora y no pude procesar tu mensaje. "
    "¿Puedes intentar de nuevo en un momento?"
)
MENSAJE_LIMITE_ITERACIONES = (
    "Perdona, se me enredó un poco el hilo. ¿Podemos seguir desde donde quedamos?"
)


@dataclass
class EstadoSesion:
    historial: list[dict] = field(default_factory=list)
    respuestas: dict[int, int] = field(default_factory=dict)
    protocolo_crisis_activo: bool = False
    test_completo: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "EstadoSesion":
        if not data:
            return cls()
        respuestas = {int(k): v for k, v in data.get("respuestas", {}).items()}
        return cls(
            historial=data.get("historial", []),
            respuestas=respuestas,
            protocolo_crisis_activo=data.get("protocolo_crisis_activo", False),
            test_completo=data.get("test_completo", False),
        )


async def _cargar_estado(sesion_id: str) -> EstadoSesion:
    return EstadoSesion.from_dict(await session_store.get(sesion_id))


async def _guardar_estado(
    sesion_id: str, estado: EstadoSesion, usuario_id: str | None = None
) -> None:
    await session_store.set(sesion_id, estado.to_dict(), usuario_id=usuario_id)


def recortar_historial(historial: list[dict], maximo: int = MAX_MENSAJES_CONTEXTO) -> list[dict]:
    """
    Deja sólo los últimos mensajes para que el hilo continuo no dispare el coste
    ni reviente la ventana de contexto.

    La continuidad de largo plazo no depende de esto: lo importante de la persona
    vive en `preferencias` y se reinyecta vía `companero.memoria_de`.

    El corte nunca puede dejar un mensaje `tool` huérfano al principio (las APIs
    lo rechazan), así que se retrocede hasta incluir el turno del asistente que
    pidió esa tool.
    """
    if len(historial) <= maximo:
        return historial

    corte = len(historial) - maximo
    while corte > 0 and historial[corte].get("role") == "tool":
        corte -= 1
    return historial[corte:]


_LINEA = "═══════════════════════════════════════════"


def contexto_ubicacion(lat: float | None, lon: float | None) -> str:
    """
    Bloque para el system prompt del turno con la ubicación de la persona.

    Va en el system prompt y NO en el mensaje del usuario: así el historial que
    ve la persona (y el que se guarda) queda limpio, y el modelo sólo conoce la
    ubicación del turno en curso.
    """
    cabecera = f"\n\n{_LINEA}\nUBICACIÓN\n{_LINEA}\n"
    if lat is None or lon is None:
        return cabecera + (
            "No hay ubicación en este turno. Si hiciera falta, puedes sugerir activarla "
            "en Perfil o dar los centros generales de Cusco con `buscar_centros_cercanos`."
        )
    zona = "aprox. en Cusco" if acc.en_cusco(lat, lon) else "fuera de Cusco"
    return cabecera + (
        f"Ubicación actual disponible: {lat:.5f}, {lon:.5f}; {zona}. "
        "No se la vuelvas a pedir ni le recites las coordenadas; "
        "`buscar_centros_cercanos` ya la usa sola."
    )


# Perú no tiene horario de verano: UTC-5 fijo (sin depender de tzdata en Windows).
ZONA_PERU = timezone(timedelta(hours=-5))
_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def contexto_fecha(ahora: datetime | None = None) -> str:
    """Fecha y hora de Perú, para que `agendar_actividad` lleve una fecha concreta."""
    ahora = (ahora or datetime.now(timezone.utc)).astimezone(ZONA_PERU)
    return (
        f"\n\n{_LINEA}\nFECHA Y HORA\n{_LINEA}\n"
        f"Ahora en Perú: {_DIAS[ahora.weekday()]} {ahora:%Y-%m-%d %H:%M} (UTC-05:00)."
    )


async def _ejecutar_tool(
    nombre: str,
    tool_input: dict,
    estado: EstadoSesion,
    usuario_id: str | None = None,
    sesion_id: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> tuple[dict, dict]:
    """
    Ejecuta la función real del backend para una tool_use dada.

    Devuelve (resultado_para_claude, señales_extra). `señales_extra` son datos
    que el router necesita para armar la respuesta HTTP (p.ej. si se debe
    pedir ubicación al cliente móvil, o las `acciones` que la app pinta como
    botones) pero que no forman parte del tool_result que se le manda de
    vuelta al modelo. `lat`/`lon` son los del turno en curso.
    """
    señales: dict[str, Any] = {}

    if nombre == "registrar_respuesta":
        item = int(tool_input["item"])
        valor = int(tool_input["valor"])
        if not (1 <= item <= N_ITEMS):
            return {"error": f"item fuera de rango: {item}"}, señales
        estado.respuestas[item] = valor
        return {"ok": True, "item": item, "valor": valor}, señales

    if nombre == "activar_protocolo_crisis":
        estado.protocolo_crisis_activo = True
        logger.warning(
            "Protocolo de crisis activado. motivo=%r nivel_urgencia=%r",
            tool_input.get("motivo"),
            tool_input.get("nivel_urgencia"),
        )
        señales["acciones"] = acc.acciones_crisis()
        return (
            {
                "protocolo_activado": True,
                "recursos_garantizados": RECURSOS_NACIONALES_PE,
                "botones_en_la_app": (
                    "Llamar a la Línea 113 y al SAMU 106, y 'Avisar a tu persona de confianza' "
                    "(la persona decide si llama o le escribe; la app NO llama ni avisa sola)."
                ),
            },
            señales,
        )

    if nombre == "ofrecer_opciones":
        opciones = acc.normalizar_opciones(tool_input.get("opciones"))
        if not opciones:
            return (
                {
                    "error": "opciones_invalidas",
                    "detalle": "Hacen falta de 2 a 5 opciones de texto distintas (≤ 32 caracteres).",
                },
                señales,
            )
        señales["opciones"] = opciones
        return {"ok": True, "opciones": opciones, "botones_en_la_app": True}, señales

    if nombre == "solicitar_ubicacion":
        señales["ubicacion_solicitada"] = True
        return (
            {
                "mensaje": (
                    "Se notificó a la app móvil para solicitar permiso de "
                    "ubicación al usuario."
                )
            },
            señales,
        )

    if nombre == "buscar_centros_cercanos":
        # Si el modelo no pasa coordenadas se usan las del turno.
        t_lat, t_lon = tool_input.get("lat"), tool_input.get("lon")
        if isinstance(t_lat, (int, float)) and isinstance(t_lon, (int, float)):
            lat, lon = float(t_lat), float(t_lon)
        resultado = await buscar_centros_cercanos(lat, lon)
        tiene_ubicacion = lat is not None and lon is not None
        fuera = tiene_ubicacion and not acc.en_cusco(lat, lon)
        señales["acciones"] = acc.acciones_centros(resultado, lat, lon, fuera_de_cusco=fuera)
        resultado = {
            **resultado,
            "ubicacion_usada": (
                "la actual de la persona" if tiene_ubicacion else "ninguna (centros generales de Cusco)"
            ),
            "botones_en_la_app": True,
        }
        if fuera:
            resultado["nota"] = (
                "La persona está fuera de Cusco y el directorio local es de Cusco. La app le "
                "muestra un botón para buscar centros en Google Maps alrededor de ella."
            )
        return resultado, señales

    if nombre == "calcular_resultado":
        respuestas = tool_input.get("respuestas")
        try:
            resultado = calcular_resultado(respuestas)
        except (ModeloNoDisponibleError, ValueError) as exc:
            logger.exception("Error al calcular el resultado ML")
            return {"error": str(exc)}, señales
        estado.test_completo = True
        # El router necesita la salida cruda del modelo para armar el resultado
        # que consume la app y para persistir la evaluación.
        señales["resultado_ml"] = resultado
        return resultado, señales

    # --- Capacidades de acompañante ---------------------------------------
    # Todas necesitan usuario identificado; sin sesión iniciada se responde algo
    # que el modelo pueda explicar sin romper la conversación.
    if nombre in {
        "recordar_dato",
        "agendar_actividad",
        "listar_actividades",
        "registrar_motivo_actividad",
    }:
        if not usuario_id:
            return {"error": "no_hay_usuario", "detalle": "La persona no ha iniciado sesión."}, señales

    try:
        if nombre == "recordar_dato":
            await repositorio.guardar_preferencia(
                usuario_id, str(tool_input.get("clave", "")), str(tool_input.get("valor", ""))
            )
            return {"ok": True, "recordado": tool_input.get("clave")}, señales

        if nombre == "agendar_actividad":
            actividad = await repositorio.crear_actividad(
                usuario_id=usuario_id,
                titulo=str(tool_input.get("titulo", "")).strip(),
                nota=tool_input.get("nota"),
                categoria=str(tool_input.get("categoria") or "general"),
                para_cuando=tool_input.get("para_cuando"),
                sesion_id=sesion_id,
            )
            señales["actividad_creada"] = True
            titulo = actividad.get("titulo") or str(tool_input.get("titulo", "")).strip()
            accion = acc.accion_actividad(titulo, actividad.get("id"))
            señales["acciones"] = [accion] if accion else []
            return {"ok": True, "actividad": titulo}, señales

        if nombre == "listar_actividades":
            pendientes = await repositorio.listar_actividades(usuario_id, estado="pendientes")
            return {"pendientes": pendientes}, señales

        if nombre == "registrar_motivo_actividad":
            actividad_id = str(tool_input.get("actividad_id", "")).strip()
            motivo = str(tool_input.get("motivo", "")).strip()
            if not actividad_id or not motivo:
                return {"error": "faltan_datos", "detalle": "Se necesita actividad_id y motivo."}, señales

            actividad = await repositorio.registrar_motivo(usuario_id, actividad_id, motivo)
            if actividad is None:
                return (
                    {"error": "no_encontrada", "detalle": "Esa actividad no existe o no es suya."},
                    señales,
                )
            return {"ok": True, "actividad": actividad.get("titulo"), "motivo": motivo}, señales
    except supabase.SupabaseError as exc:
        logger.exception("Fallo de base de datos ejecutando la tool %s", nombre)
        return {"error": "base_de_datos", "detalle": exc.mensaje}, señales

    if nombre == "recomendar_musica":
        busqueda = str(tool_input.get("busqueda", ""))
        accion = acc.accion_musica(busqueda)
        señales["acciones"] = [accion] if accion else []
        return enlaces_musica(busqueda), señales

    if nombre == "recomendar_recurso":
        accion = acc.accion_recurso(str(tool_input.get("recurso_id", "")))
        if accion is None:
            return {"error": "recurso_desconocido", "validos": list(acc.RECURSOS_APP)}, señales
        señales["acciones"] = [accion]
        return {"ok": True, "recurso": accion["titulo"], "boton_en_la_app": True}, señales

    logger.warning("Tool desconocida solicitada por el modelo: %s", nombre)
    return {"error": f"tool desconocida: {nombre}"}, señales


async def procesar_mensaje(
    sesion_id: str,
    mensaje_usuario: str,
    lat: float | None = None,
    lon: float | None = None,
    usuario_id: str | None = None,
) -> dict:
    """
    Procesa un turno de conversación completo para una sesión.

    Devuelve un dict con: respuesta (texto final del asistente),
    protocolo_crisis_activo, test_completo, ubicacion_solicitada, `acciones`
    (botones derivados de las tools del turno; puede ser []), `opciones`
    (respuestas rápidas de `ofrecer_opciones`; puede ser []) y —sólo en el
    turno donde el modelo llama a `calcular_resultado`— `resultado` (contrato de
    la app), `resultado_ml` (salida cruda del SVM) y `respuestas`.
    """
    estado = await _cargar_estado(sesion_id)

    # El mensaje se guarda tal cual lo escribió la persona; la ubicación del
    # turno viaja en el system prompt (ver `contexto_ubicacion`).
    estado.historial.append({"role": "user", "content": mensaje_usuario})

    proveedor = get_proveedor()
    ubicacion_solicitada = False
    acciones: list[dict] = []
    # Respuestas rápidas de la última pregunta: si el modelo llama varias veces
    # a `ofrecer_opciones` en el turno, vale la última válida. No se persisten.
    opciones: list[str] = []
    resultado_ml: dict | None = None
    texto_final = ""

    # El hilo es continuo, así que al modelo se le manda el prompt + lo que
    # recordamos de la persona, y sólo la ventana reciente de mensajes.
    # El bloque de seguimiento (modo recuperación) lo decide el backend con las
    # evaluaciones guardadas; ver `app/chat/seguimiento.py`.
    test_a_medias = 0 if estado.test_completo else len(estado.respuestas)
    system = (
        SYSTEM_PROMPT
        + await memoria_de(usuario_id)
        + await contexto_seguimiento(usuario_id, test_a_medias)
        + contexto_ubicacion(lat, lon)
        + contexto_fecha()
    )

    for _ in range(MAX_TOOL_ITERATIONS):
        try:
            respuesta = await proveedor.generar(
                system=system,
                mensajes=recortar_historial(estado.historial),
                tools=TOOLS,
                max_tokens=MAX_TOKENS,
            )
        except LLMError:
            logger.exception("Error llamando al proveedor de texto (%s)", proveedor.nombre)
            texto_final = MENSAJE_ERROR_API
            break

        estado.historial.append(respuesta.a_mensaje())

        if respuesta.texto:
            texto_final = respuesta.texto

        if not respuesta.pide_tools:
            break

        for llamada in respuesta.tool_calls:
            resultado, señales = await _ejecutar_tool(
                llamada.nombre,
                llamada.argumentos,
                estado,
                usuario_id=usuario_id,
                sesion_id=sesion_id,
                lat=lat,
                lon=lon,
            )
            acc.agregar(acciones, señales.get("acciones") or [])
            if señales.get("opciones"):
                opciones = señales["opciones"]
            if señales.get("ubicacion_solicitada"):
                ubicacion_solicitada = True
            if señales.get("resultado_ml"):
                resultado_ml = señales["resultado_ml"]

            estado.historial.append(
                {
                    "role": "tool",
                    "tool_call_id": llamada.id,
                    "name": llamada.nombre,
                    "content": json.dumps(resultado, ensure_ascii=False),
                }
            )
    else:
        logger.warning("Se alcanzó el límite de iteraciones de tool-use en la sesión %s", sesion_id)
        if not texto_final:
            texto_final = MENSAJE_LIMITE_ITERACIONES

    await _guardar_estado(sesion_id, estado, usuario_id=usuario_id)

    return {
        "respuesta": texto_final,
        "protocolo_crisis_activo": estado.protocolo_crisis_activo,
        "test_completo": estado.test_completo,
        "ubicacion_solicitada": ubicacion_solicitada,
        "acciones": acciones,
        "opciones": opciones,
        "resultado": construir_resultado(resultado_ml, sesion_id) if resultado_ml else None,
        "resultado_ml": resultado_ml,
        "respuestas": dict(estado.respuestas),
    }
