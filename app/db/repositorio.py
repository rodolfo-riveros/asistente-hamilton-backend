"""
Persistencia de dominio sobre Supabase.

Todas las funciones filtran por `usuario_id` explícitamente: el backend usa la
service role key, que ignora RLS, así que el aislamiento entre usuarios se
garantiza aquí (ver nota en `app/db/supabase.py`).

Los errores de red/BD se propagan como `SupabaseError`; quien llama decide si
son fatales. Para escrituras "de mejor esfuerzo" (transcripción de mensajes)
usa `guardar_mensajes`, que registra el fallo y no interrumpe la conversación.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db import supabase

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Perfiles
# --------------------------------------------------------------------------- #


async def upsert_perfil(usuario_id: str, nombre: str, email: str | None) -> None:
    """Crea/actualiza el perfil de aplicación asociado a un usuario de Auth."""
    await supabase.insert(
        "perfiles",
        {"id": usuario_id, "nombre": nombre, "email": email},
        upsert=True,
    )


async def obtener_perfil(usuario_id: str) -> dict | None:
    filas = await supabase.select(
        "perfiles", {"id": f"eq.{usuario_id}", "select": "id,nombre,email", "limit": 1}
    )
    return filas[0] if filas else None


# --------------------------------------------------------------------------- #
# Mensajes (transcripción)
# --------------------------------------------------------------------------- #


# `mensajes.acciones` llega con schema_v4. Hasta que se aplique, el backend
# sigue funcionando sin ella: reintenta sin la columna y avisa UNA vez.
_aviso_sin_columna_acciones = False


def _es_error_columna_acciones(exc: supabase.SupabaseError) -> bool:
    """¿El fallo se debe a que la columna `acciones` aún no existe?"""
    texto = (exc.mensaje or "").lower()
    return exc.status_code in (400, 404) and (
        "acciones" in texto or "pgrst204" in texto or "column" in texto or "columna" in texto
    )


def _avisar_sin_columna_acciones() -> None:
    global _aviso_sin_columna_acciones
    if not _aviso_sin_columna_acciones:
        _aviso_sin_columna_acciones = True
        logger.warning(
            "La columna mensajes.acciones no existe: ejecuta supabase/schema_v4.sql. "
            "Mientras tanto los botones del chat no se guardan en el historial."
        )


async def guardar_mensajes(
    sesion_id: str,
    usuario_id: str | None,
    mensajes: list[tuple[str, str] | tuple[str, str, list[dict] | None]],
) -> None:
    """
    Guarda los mensajes de un turno: tuplas (rol, contenido) o (rol, contenido,
    acciones). Best-effort: si falla, se registra y la conversación continúa —
    perder la transcripción no debe cortar el chat.

    La columna `acciones` sólo se envía si el turno trae alguna (PostgREST exige
    las mismas claves en todas las filas, así que entonces va en todas, con null
    donde no hay). Si la columna aún no existe, se reintenta sin ella.
    """
    filas = []
    for mensaje in mensajes:
        rol, contenido = mensaje[0], mensaje[1]
        acciones = mensaje[2] if len(mensaje) > 2 else None
        if contenido:
            filas.append(
                {
                    "sesion_id": sesion_id,
                    "usuario_id": usuario_id,
                    "rol": rol,
                    "contenido": contenido,
                    "acciones": acciones or None,
                }
            )
    if not filas:
        return

    con_acciones = any(f["acciones"] for f in filas)
    if not con_acciones:
        for fila in filas:
            del fila["acciones"]

    try:
        await supabase.insert("mensajes", filas)
        return
    except supabase.SupabaseError as exc:
        if not (con_acciones and _es_error_columna_acciones(exc)):
            logger.exception("No se pudo guardar la transcripción de la sesión %s", sesion_id)
            return
        _avisar_sin_columna_acciones()

    try:
        sin_acciones = [{k: v for k, v in f.items() if k != "acciones"} for f in filas]
        await supabase.insert("mensajes", sin_acciones)
    except supabase.SupabaseError:
        logger.exception("No se pudo guardar la transcripción de la sesión %s", sesion_id)


async def _select_mensajes(params: dict[str, Any]) -> list[dict]:
    """
    Lee mensajes pidiendo también `acciones`; si la columna aún no existe
    (schema_v4 sin aplicar), repite la consulta sin ella. `acciones` siempre
    sale como lista (null -> []).
    """
    try:
        filas = await supabase.select(
            "mensajes", {**params, "select": "rol,contenido,creado_en,acciones"}
        )
    except supabase.SupabaseError as exc:
        if not _es_error_columna_acciones(exc):
            raise
        _avisar_sin_columna_acciones()
        filas = await supabase.select("mensajes", {**params, "select": "rol,contenido,creado_en"})

    for fila in filas:
        acciones = fila.get("acciones")
        fila["acciones"] = acciones if isinstance(acciones, list) else []
    return filas


# --------------------------------------------------------------------------- #
# Evaluaciones
# --------------------------------------------------------------------------- #


async def listar_sesiones(usuario_id: str, limite: int = 20) -> list[dict]:
    """Sesiones del usuario, la más reciente primero."""
    return await supabase.select(
        "sesiones",
        {
            "usuario_id": f"eq.{usuario_id}",
            "select": "id,test_completo,protocolo_crisis_activo,creado_en,actualizado_en",
            "order": "actualizado_en.desc",
            "limit": limite,
        },
    )


async def sesion_utilizable(sesion_id: str, usuario_id: str | None) -> bool:
    """
    ¿Puede este usuario (o un anónimo) continuar la sesión `sesion_id`?

    - id que no es UUID -> no (PostgREST fallaría con la columna uuid).
    - sesión inexistente -> sí (se creará con ese id, como antes).
    - sesión anónima -> sí (un usuario puede "adoptar" su charla anónima).
    - sesión de otra persona -> no.
    """
    try:
        uuid.UUID(str(sesion_id))
    except ValueError:
        return False

    filas = await supabase.select(
        "sesiones", {"id": f"eq.{sesion_id}", "select": "usuario_id", "limit": 1}
    )
    if not filas:
        return True
    dueno = filas[0].get("usuario_id")
    return dueno is None or dueno == usuario_id


async def obtener_sesion(sesion_id: str, usuario_id: str) -> dict | None:
    filas = await supabase.select(
        "sesiones",
        {
            "id": f"eq.{sesion_id}",
            "usuario_id": f"eq.{usuario_id}",
            "select": "id,test_completo,protocolo_crisis_activo,creado_en,actualizado_en",
            "limit": 1,
        },
    )
    return filas[0] if filas else None


async def obtener_mensajes(sesion_id: str, usuario_id: str, limite: int = 200) -> list[dict]:
    """
    Transcripción de una conversación, en orden cronológico.

    Se filtra también por `usuario_id` porque el backend consulta con la service
    role key, que ignora RLS: el aislamiento lo garantizamos aquí.
    """
    return await _select_mensajes(
        {
            "sesion_id": f"eq.{sesion_id}",
            "usuario_id": f"eq.{usuario_id}",
            "order": "creado_en.asc",
            "limit": limite,
        }
    )


async def guardar_evaluacion(
    sesion_id: str,
    usuario_id: str | None,
    resultado: dict,
    prediccion_ml: int | None = None,
    respuestas: dict[int, int] | None = None,
) -> None:
    """Guarda el resultado final del test (lo que lee la pantalla Histórico)."""
    fila: dict[str, Any] = {
        "sesion_id": sesion_id,
        "usuario_id": usuario_id,
        "score": resultado["score"],
        "nivel": resultado["nivel"],
        "interpretacion": resultado["interpretacion"],
        "recomendaciones": resultado.get("recomendaciones", []),
        "prediccion_ml": prediccion_ml,
        # jsonb no admite claves int: se serializan como string.
        "respuestas": {str(k): v for k, v in (respuestas or {}).items()},
    }
    await supabase.insert("evaluaciones", fila)


async def listar_evaluaciones(usuario_id: str, limite: int = 50) -> list[dict]:
    """
    Devuelve el histórico del usuario en el contrato que consume la app
    (`HamiltonResult` de `src/types/chat.ts`), más reciente primero.
    """
    filas = await supabase.select(
        "evaluaciones",
        {
            "usuario_id": f"eq.{usuario_id}",
            "select": "sesion_id,score,nivel,interpretacion,recomendaciones,creado_en",
            "order": "creado_en.desc",
            "limit": limite,
        },
    )

    return [
        {
            "sesion_id": fila.get("sesion_id") or "",
            "score": fila.get("score", 0),
            "nivel": fila.get("nivel", ""),
            "interpretacion": fila.get("interpretacion", ""),
            "recomendaciones": fila.get("recomendaciones") or [],
            "fecha": fila.get("creado_en"),
        }
        for fila in filas
    ]


# --------------------------------------------------------------------------- #
# Hilo único de conversación (estilo mensajería)
# --------------------------------------------------------------------------- #


async def sesion_activa(usuario_id: str) -> str | None:
    """
    Id del hilo de conversación del usuario.

    La app no maneja sesiones: hay UN hilo continuo por persona, como en
    WhatsApp. Se reutiliza el más reciente y sólo se crea uno nuevo si no hay
    ninguno.
    """
    filas = await supabase.select(
        "sesiones",
        {
            "usuario_id": f"eq.{usuario_id}",
            "select": "id",
            "order": "actualizado_en.desc",
            "limit": 1,
        },
    )
    return filas[0]["id"] if filas else None


async def historial_usuario(usuario_id: str, limite: int = 200) -> list[dict]:
    """
    Últimos mensajes del usuario en todos sus hilos, en orden cronológico.

    Se pide en orden descendente para quedarnos con los MÁS RECIENTES cuando hay
    más de `limite`, y se devuelve invertido para pintarlos de arriba a abajo.
    """
    filas = await _select_mensajes(
        {
            "usuario_id": f"eq.{usuario_id}",
            "order": "creado_en.desc",
            "limit": limite,
        }
    )
    return list(reversed(filas))


# --------------------------------------------------------------------------- #
# Memoria a largo plazo
# --------------------------------------------------------------------------- #


async def guardar_preferencia(usuario_id: str, clave: str, valor: str) -> None:
    """Recuerda un dato de la persona (gustos, rutinas, nombres...)."""
    await supabase.insert(
        "preferencias",
        {"usuario_id": usuario_id, "clave": clave.strip().lower(), "valor": valor.strip()},
        upsert=True,
    )


async def listar_preferencias(usuario_id: str, limite: int = 40) -> list[dict]:
    return await supabase.select(
        "preferencias",
        {
            "usuario_id": f"eq.{usuario_id}",
            "select": "clave,valor",
            "order": "actualizado_en.desc",
            "limit": limite,
        },
    )


# --------------------------------------------------------------------------- #
# Actividades acordadas
# --------------------------------------------------------------------------- #


async def crear_actividad(
    usuario_id: str,
    titulo: str,
    nota: str | None = None,
    categoria: str = "general",
    para_cuando: str | None = None,
    sesion_id: str | None = None,
) -> dict:
    filas = await supabase.insert(
        "actividades",
        {
            "usuario_id": usuario_id,
            "sesion_id": sesion_id,
            "titulo": titulo,
            "nota": nota,
            "categoria": categoria,
            "para_cuando": para_cuando,
        },
    )
    return filas[0] if filas else {}


CAMPOS_ACTIVIDAD = (
    "id,titulo,nota,categoria,para_cuando,completada,completada_en,"
    "motivo_no_realizada,creado_en"
)

ESTADOS_ACTIVIDAD = ("pendientes", "vencidas", "completadas", "todas")


def _a_datetime(valor: Any) -> datetime | None:
    """
    Convierte una fecha ISO de PostgREST a `datetime` con zona horaria.

    Postgres devuelve `timestamptz` en ISO 8601 (`2026-07-28T18:00:00+00:00`),
    pero se aceptan también el sufijo `Z` y las fechas sin zona (que se asumen
    UTC) para no depender del formato exacto del driver.
    """
    if isinstance(valor, datetime):
        fecha = valor
    elif isinstance(valor, str) and valor.strip():
        try:
            fecha = datetime.fromisoformat(valor.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    return fecha if fecha.tzinfo else fecha.replace(tzinfo=timezone.utc)


def esta_vencida(fila: dict, ahora: datetime | None = None) -> bool:
    """
    `True` si la actividad ya pasó su fecha y sigue sin completarse.

    Se calcula aquí y no se guarda en la base: si fuera una columna habría que
    recorrer la tabla cada cierto tiempo para mantenerla al día. Una actividad
    sin `para_cuando` nunca vence.
    """
    if fila.get("completada"):
        return False

    para_cuando = _a_datetime(fila.get("para_cuando"))
    if para_cuando is None:
        return False

    return para_cuando < (ahora or datetime.now(timezone.utc))


async def listar_actividades(
    usuario_id: str, estado: str = "pendientes", limite: int = 50
) -> list[dict]:
    """
    Actividades del usuario, con la bandera `vencida` ya calculada.

    `estado`:
    - `pendientes` (por defecto): todo lo que no está hecho, vencido o no. La
      app las muestra juntas y destaca las vencidas con la bandera.
    - `vencidas`: sólo las no hechas cuya fecha ya pasó.
    - `completadas`: sólo las hechas.
    - `todas`: sin filtro.
    """
    if estado not in ESTADOS_ACTIVIDAD:
        estado = "pendientes"

    ahora = datetime.now(timezone.utc)
    params: dict[str, Any] = {
        "usuario_id": f"eq.{usuario_id}",
        "select": CAMPOS_ACTIVIDAD,
        "order": "completada.asc,para_cuando.asc,creado_en.desc",
        "limit": limite,
    }

    if estado in ("pendientes", "vencidas"):
        params["completada"] = "eq.false"
    elif estado == "completadas":
        params["completada"] = "eq.true"

    if estado == "vencidas":
        # Se filtra también en la base para que `limite` cuente sólo vencidas.
        params["para_cuando"] = f"lt.{ahora.isoformat()}"

    filas = await supabase.select("actividades", params)
    return [{**fila, "vencida": esta_vencida(fila, ahora)} for fila in filas]


async def marcar_actividad(usuario_id: str, actividad_id: str, completada: bool) -> dict | None:
    """Marca/desmarca una actividad y mantiene `completada_en` en coherencia."""
    valores = {
        "completada": completada,
        "completada_en": datetime.now(timezone.utc).isoformat() if completada else None,
    }
    filas = await supabase.update(
        "actividades",
        valores,
        {
            "id": f"eq.{actividad_id}",
            "usuario_id": f"eq.{usuario_id}",
            "select": CAMPOS_ACTIVIDAD,
        },
    )
    if not filas:
        return None
    return {**filas[0], "vencida": esta_vencida(filas[0])}


async def eliminar_actividad(usuario_id: str, actividad_id: str) -> bool:
    """Borra una actividad del usuario. `False` si no existía (o no es suya)."""
    filas = await supabase.delete(
        "actividades",
        {"id": f"eq.{actividad_id}", "usuario_id": f"eq.{usuario_id}", "select": "id"},
    )
    return bool(filas)


async def registrar_motivo(usuario_id: str, actividad_id: str, motivo: str) -> dict | None:
    """
    Guarda por qué la persona no pudo hacer una actividad.

    Es material clínico: aparece en la ficha de diagnóstico, así que se conserva
    tal como lo contó, sin reinterpretarlo.
    """
    filas = await supabase.update(
        "actividades",
        {"motivo_no_realizada": motivo.strip()},
        {
            "id": f"eq.{actividad_id}",
            "usuario_id": f"eq.{usuario_id}",
            "select": CAMPOS_ACTIVIDAD,
        },
    )
    if not filas:
        return None
    return {**filas[0], "vencida": esta_vencida(filas[0])}


# --------------------------------------------------------------------------- #
# Perfil ampliado
# --------------------------------------------------------------------------- #


async def actualizar_perfil(
    usuario_id: str, campos: dict, permitir_nulos: bool = False
) -> dict | None:
    """
    Actualiza sólo los campos presentes (nombre, edad, ciudad, bio, foto...).

    Con `permitir_nulos=True` un `None` explícito borra el dato (lo usa
    `PUT /perfil` cuando la persona vacía un campo); si no, se ignora.
    """
    permitidos = {"nombre", "edad", "ciudad", "ocupacion", "bio", "foto_url"}
    datos = {
        k: v for k, v in campos.items() if k in permitidos and (permitir_nulos or v is not None)
    }
    if not datos:
        return await obtener_perfil(usuario_id)

    filas = await supabase.update("perfiles", datos, {"id": f"eq.{usuario_id}"})
    return filas[0] if filas else None


async def perfil_completo(usuario_id: str) -> dict | None:
    filas = await supabase.select(
        "perfiles",
        {
            "id": f"eq.{usuario_id}",
            "select": "id,nombre,email,foto_url,edad,ciudad,ocupacion,bio",
            "limit": 1,
        },
    )
    return filas[0] if filas else None


# --------------------------------------------------------------------------- #
# Ficha de diagnóstico
# --------------------------------------------------------------------------- #


async def respuestas_ultima_sesion(usuario_id: str) -> dict:
    """
    Respuestas del test registradas en la sesión más reciente del usuario.

    Vienen del blob `sesiones.estado` (lo que guarda el orquestador), con las
    claves como string porque jsonb no admite enteros. Normalizarlas es cosa de
    `app.chat.items.normalizar_respuestas`.
    """
    filas = await supabase.select(
        "sesiones",
        {
            "usuario_id": f"eq.{usuario_id}",
            "select": "estado",
            "order": "actualizado_en.desc",
            "limit": 1,
        },
    )
    if not filas:
        return {}
    estado = filas[0].get("estado") or {}
    respuestas = estado.get("respuestas") if isinstance(estado, dict) else None
    return respuestas if isinstance(respuestas, dict) else {}


async def estadisticas_usuario(usuario_id: str) -> dict:
    """
    Conteos de uso para la ficha de diagnóstico.

    Los totales se piden con `count=exact` (no se traen las filas) y las fechas
    de la primera/última conversación salen de `sesiones`, que es lo que la
    persona percibe como "una conversación".
    """
    conversaciones = await supabase.contar("sesiones", {"usuario_id": f"eq.{usuario_id}"})
    mensajes = await supabase.contar("mensajes", {"usuario_id": f"eq.{usuario_id}"})
    creadas = await supabase.contar("actividades", {"usuario_id": f"eq.{usuario_id}"})
    completadas = await supabase.contar(
        "actividades", {"usuario_id": f"eq.{usuario_id}", "completada": "eq.true"}
    )

    primera = await supabase.select(
        "sesiones",
        {
            "usuario_id": f"eq.{usuario_id}",
            "select": "creado_en",
            "order": "creado_en.asc",
            "limit": 1,
        },
    )
    ultima = await supabase.select(
        "sesiones",
        {
            "usuario_id": f"eq.{usuario_id}",
            "select": "actualizado_en",
            "order": "actualizado_en.desc",
            "limit": 1,
        },
    )

    return {
        "conversaciones": conversaciones,
        "mensajes": mensajes,
        "primera_conversacion": primera[0]["creado_en"] if primera else None,
        "ultima_conversacion": ultima[0]["actualizado_en"] if ultima else None,
        "actividades_creadas": creadas,
        "actividades_completadas": completadas,
        "tasa_cumplimiento": round(completadas * 100 / creadas) if creadas else 0,
    }
