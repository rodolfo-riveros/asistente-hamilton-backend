"""
Acciones que acompañan a un mensaje de Aurora (botones en el chat de la app).

El texto del asistente cuenta, las acciones permiten HACER: abrir Spotify,
ir a un recurso de la app, ver un centro en Google Maps, llamar a la Línea
113 o ver la actividad recién agendada. Se derivan de las tools que el modelo
llamó en el turno, nunca de lo que escribe: así no hay enlaces inventados.

Contrato (ver nota "Chat como amiga" de la app), discriminado por `tipo`:

    musica      {titulo, busqueda, spotify_url, youtube_music_url}
    recurso     {titulo, recurso_id}
    centro      {titulo, direccion?, telefono?, maps_url, distancia_km?}
    buscar_mapa {titulo, maps_url}
    llamar      {titulo, telefono}
    actividad   {titulo, actividad_id?}
"""

from __future__ import annotations

import math
from typing import Any
from urllib.parse import quote, quote_plus

from app.locations.centros import CUSCO_CENTRO_LAT, CUSCO_CENTRO_LON
from app.safety.recursos import RECURSOS_NACIONALES_PE

# Recursos de la pestaña Recursos de la app (`src/screens/recursos/contenido.ts`).
# Los ids son el contrato; los títulos, los que ve la persona en la app.
RECURSOS_APP: dict[str, str] = {
    "guia-depresion": "Entender la depresión sin culparte",
    "respiracion-478": "Respiración 4-7-8",
    "escaneo-corporal": "Escaneo corporal para dormir",
    "tres-cosas-buenas": "Tres cosas buenas de hoy",
    "grounding-54321": "Grounding 5-4-3-2-1",
    "higiene-sueno": "Higiene del sueño",
    "activacion-conductual": "Activación conductual: una pequeña tarea",
}

# Radio alrededor de la Plaza de Armas que consideramos "en Cusco": cubre la
# ciudad y sus distritos (Santiago, San Sebastián, San Jerónimo, Saylla...),
# que es donde está el directorio local de centros.
RADIO_CUSCO_KM = 25.0

MAX_CENTROS = 3


# --------------------------------------------------------------------------- #
# Geografía
# --------------------------------------------------------------------------- #


def distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en línea recta (haversine)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def en_cusco(lat: float, lon: float) -> bool:
    return distancia_km(lat, lon, CUSCO_CENTRO_LAT, CUSCO_CENTRO_LON) <= RADIO_CUSCO_KM


def _coord(valor: float) -> str:
    return f"{valor:.5f}".rstrip("0").rstrip(".")


def url_maps_coordenadas(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={_coord(lat)},{_coord(lon)}"


def url_maps_texto(texto: str) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(texto)}"


def url_buscar_cerca(lat: float, lon: float) -> str:
    """Búsqueda de centros de salud mental centrada en la ubicación de la persona."""
    return (
        "https://www.google.com/maps/search/centro+de+salud+mental/"
        f"@{_coord(lat)},{_coord(lon)},14z"
    )


# --------------------------------------------------------------------------- #
# Constructores por tool
# --------------------------------------------------------------------------- #


def accion_musica(busqueda: str) -> dict | None:
    busqueda = (busqueda or "").strip()
    if not busqueda:
        return None
    return {
        "tipo": "musica",
        "titulo": busqueda[:1].upper() + busqueda[1:],
        "busqueda": busqueda,
        "spotify_url": f"https://open.spotify.com/search/{quote(busqueda)}",
        "youtube_music_url": f"https://music.youtube.com/search?q={quote_plus(busqueda)}",
    }


def accion_recurso(recurso_id: str) -> dict | None:
    recurso_id = (recurso_id or "").strip()
    titulo = RECURSOS_APP.get(recurso_id)
    if not titulo:
        return None
    return {"tipo": "recurso", "titulo": titulo, "recurso_id": recurso_id}


def acciones_crisis() -> list[dict]:
    linea = RECURSOS_NACIONALES_PE["linea_113"]
    samu = RECURSOS_NACIONALES_PE["samu"]
    return [
        {"tipo": "llamar", "titulo": "Línea 113, opción 5", "telefono": linea["telefono"]},
        {"tipo": "llamar", "titulo": "SAMU 106 (emergencias)", "telefono": samu["telefono"]},
    ]


def accion_actividad(titulo: str, actividad_id: Any = None) -> dict | None:
    titulo = (titulo or "").strip()
    if not titulo:
        return None
    return {
        "tipo": "actividad",
        "titulo": titulo,
        "actividad_id": str(actividad_id) if actividad_id else None,
    }


def _accion_centro(centro: dict, lat: float | None, lon: float | None) -> dict | None:
    nombre = (centro.get("nombre") or "").strip()
    if not nombre:
        return None

    c_lat, c_lon = centro.get("lat"), centro.get("lon")
    tiene_coord = isinstance(c_lat, (int, float)) and isinstance(c_lon, (int, float))
    direccion = (centro.get("direccion") or "").strip() or None

    if tiene_coord:
        maps_url = url_maps_coordenadas(c_lat, c_lon)
    else:
        # Sin coordenadas: búsqueda por nombre + dirección (o distrito) + Cusco.
        partes = [nombre, direccion or (centro.get("distrito") or ""), "Cusco"]
        maps_url = url_maps_texto(", ".join(p for p in partes if p))

    distancia = None
    if tiene_coord and lat is not None and lon is not None:
        distancia = round(distancia_km(lat, lon, c_lat, c_lon), 1)

    # Sólo lo que trae el directorio o Google: nunca se inventan datos.
    return {
        "tipo": "centro",
        "titulo": nombre,
        "direccion": direccion,
        "telefono": (centro.get("telefono") or "").strip() or None,
        "maps_url": maps_url,
        "distancia_km": distancia,
    }


def acciones_centros(
    resultado: dict, lat: float | None, lon: float | None, *, fuera_de_cusco: bool
) -> list[dict]:
    """
    Hasta 3 centros + un botón para buscar en Google Maps alrededor de la persona.

    Fuera de Cusco el directorio local queda lejos: se muestra primero (y sólo)
    la búsqueda en el mapa, salvo que Google Places haya devuelto centros cerca.
    """
    centros = [c for c in resultado.get("centros_cercanos") or [] if isinstance(c, dict)]
    if fuera_de_cusco and not resultado.get("desde_google"):
        centros = []

    acciones = [a for a in (_accion_centro(c, lat, lon) for c in centros) if a]
    acciones.sort(key=lambda a: a["distancia_km"] if a["distancia_km"] is not None else math.inf)
    acciones = acciones[:MAX_CENTROS]

    if lat is not None and lon is not None:
        mapa = {
            "tipo": "buscar_mapa",
            "titulo": "Buscar centros de salud mental cerca de ti",
            "maps_url": url_buscar_cerca(lat, lon),
        }
        acciones = [mapa, *acciones] if fuera_de_cusco else [*acciones, mapa]
    return acciones


# --------------------------------------------------------------------------- #
# Acumulación sin duplicados
# --------------------------------------------------------------------------- #


def _clave(accion: dict) -> tuple:
    tipo = accion.get("tipo")
    distintivo = {
        "musica": "busqueda",
        "recurso": "recurso_id",
        "centro": "maps_url",
        "buscar_mapa": "maps_url",
        "llamar": "telefono",
        "actividad": "actividad_id",
    }.get(tipo, "titulo")
    valor = accion.get(distintivo) or accion.get("titulo")
    if isinstance(valor, str):
        valor = valor.strip().lower()
    return tipo, valor


def agregar(destino: list[dict], nuevas: list[dict | None]) -> None:
    """Añade las acciones nuevas a `destino`, saltando las repetidas."""
    vistas = {_clave(a) for a in destino}
    for accion in nuevas:
        if not accion:
            continue
        clave = _clave(accion)
        if clave in vistas:
            continue
        vistas.add(clave)
        destino.append(accion)
