"""
Capacidades de "amigo" del asistente: memoria, actividades y música.

Separado del orquestador para que éste siga siendo sólo el loop de tool-use.
Todo lo de aquí es best-effort: si Supabase falla, el asistente sigue
conversando, simplemente no recuerda o no agenda.
"""

from __future__ import annotations

import logging
from urllib.parse import quote_plus

from app.db import repositorio, supabase

logger = logging.getLogger(__name__)

# Cuántos datos recordados se inyectan en el prompt. Suficiente para que suene
# a alguien que te conoce, sin inflar cada petición.
MAX_MEMORIA = 25

# Actividades pendientes que viajan en la memoria (vencidas incluidas).
MAX_ACTIVIDADES = 10


def _describir(actividad: dict) -> str:
    """Título + fecha prevista, en corto, para el bloque de memoria."""
    fecha = actividad.get("para_cuando")
    return f"{actividad.get('titulo', '')}" + (f" (para {fecha[:10]})" if fecha else "")


def enlaces_musica(busqueda: str) -> dict:
    """
    Construye enlaces de búsqueda reales, sin necesitar API ni OAuth.

    Se usan búsquedas en vez de playlists concretas a propósito: no se puede
    garantizar que un id de playlist siga existiendo, y una búsqueda siempre
    lleva a resultados vivos.
    """
    q = quote_plus(busqueda.strip())
    return {
        "busqueda": busqueda.strip(),
        "spotify": f"https://open.spotify.com/search/{q}",
        "youtube": f"https://www.youtube.com/results?search_query={q}",
        "youtube_music": f"https://music.youtube.com/search?q={q}",
    }


async def memoria_de(usuario_id: str | None) -> str:
    """
    Bloque de texto con lo que sabemos de la persona, para añadir al system
    prompt. Es lo que permite continuidad real aunque el historial se recorte.
    """
    if not usuario_id:
        return ""

    try:
        perfil = await repositorio.perfil_completo(usuario_id)
        preferencias = await repositorio.listar_preferencias(usuario_id, limite=MAX_MEMORIA)
        actividades = await repositorio.listar_actividades(
            usuario_id, estado="pendientes", limite=MAX_ACTIVIDADES
        )
    except supabase.SupabaseError:
        logger.exception("No se pudo cargar la memoria del usuario %s", usuario_id)
        return ""

    partes: list[str] = []

    if perfil:
        datos = [
            f"nombre: {perfil.get('nombre')}" if perfil.get("nombre") else "",
            f"edad: {perfil['edad']}" if perfil.get("edad") else "",
            f"ciudad: {perfil['ciudad']}" if perfil.get("ciudad") else "",
            f"ocupación: {perfil['ocupacion']}" if perfil.get("ocupacion") else "",
            f"sobre sí: {perfil['bio']}" if perfil.get("bio") else "",
        ]
        datos = [d for d in datos if d]
        if datos:
            partes.append("Perfil: " + "; ".join(datos))

    if preferencias:
        recordado = "; ".join(f"{p['clave']}: {p['valor']}" for p in preferencias)
        partes.append("Lo que ya sabes de la persona: " + recordado)

    # Las vencidas se separan y llevan su id: el modelo las necesita para poder
    # preguntar por ellas y llamar a `registrar_motivo_actividad`.
    vencidas = [a for a in actividades if a.get("vencida") and not a.get("motivo_no_realizada")]
    por_venir = [a for a in actividades if a not in vencidas]

    if por_venir:
        partes.append(
            "Actividades que acordaron y siguen pendientes: "
            + "; ".join(_describir(a) for a in por_venir)
        )

    if vencidas:
        partes.append(
            "Actividades VENCIDAS sin completar (pregúntale por ellas con calidez, sin "
            "regañar, y si te cuenta qué se lo impidió llama a "
            "`registrar_motivo_actividad` con su id): "
            + "; ".join(f"{_describir(a)} [id: {a['id']}]" for a in vencidas)
        )

    if not partes:
        return ""

    return (
        "\n\n═══════════════════════════════════════════\n"
        "MEMORIA DE ESTA PERSONA (no la recites; úsala con naturalidad)\n"
        "═══════════════════════════════════════════\n" + "\n".join(f"- {p}" for p in partes)
    )
