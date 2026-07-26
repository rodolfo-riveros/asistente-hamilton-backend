"""
Búsqueda de centros de salud mental cercanos.

Estrategia en dos niveles (ver sección 9 del documento de especificación):

Nivel 1 (principal): Google Places API, con sesgo geográfico a Cusco. Si hay
`GOOGLE_PLACES_API_KEY` configurada, se hace un Nearby Search con las
coordenadas reales del usuario; si no hay ubicación disponible, se usa como
centro por defecto Cusco Cercado.

Nivel 2 (fallback local): directorio semilla `data/csmc_cusco.json`. Se usa si
no hay API key o si Google Places no devuelve resultados (frecuente en
provincias con poca cobertura de mapas). Mientras ese JSON tenga
`direccion`/`lat`/`lon` en null, solo se devuelve como orientación general
(nombre + provincia/distrito) — nunca se inventan datos de contacto.

En ambos casos, el resultado se entrega SIEMPRE junto con los recursos
nacionales garantizados (Línea 113, SAMU 106) — la búsqueda de centros nunca
los reemplaza, solo los complementa.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from app.config import settings
from app.safety.recursos import RECURSOS_NACIONALES_PE

logger = logging.getLogger(__name__)

# Plaza de Armas, Cusco — centro por defecto cuando no hay ubicación del usuario.
CUSCO_CENTRO_LAT = -13.5319
CUSCO_CENTRO_LON = -71.9675

_KEYWORD_BUSQUEDA = "centro de salud mental OR psiquiatria OR psicologia OR hospital"


def cargar_directorio_local_csmc(path: Path | None = None) -> list[dict]:
    """Lee el directorio semilla local de centros de salud mental (Cusco)."""
    ruta = path or settings.csmc_json_path
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
    except FileNotFoundError:
        logger.warning("No se encontró el directorio local de CSMC en %s", ruta)
        return []
    except json.JSONDecodeError:
        logger.exception("El directorio local de CSMC en %s no es JSON válido", ruta)
        return []

    resultados = []
    for centro in datos:
        resultados.append(
            {
                "nombre": centro.get("nombre"),
                "provincia": centro.get("provincia"),
                "distrito": centro.get("distrito"),
                "direccion": centro.get("direccion"),
                "telefono": centro.get("telefono"),
                "lat": centro.get("lat"),
                "lon": centro.get("lon"),
                "fuente": centro.get("fuente"),
            }
        )
    return resultados


async def _buscar_google_places(lat: float, lon: float, radio: int) -> list[dict]:
    """Nearby Search de Google Places. Devuelve [] si no hay API key o falla la llamada."""
    api_key = settings.google_places_api_key
    if not api_key:
        return []

    try:
        async with httpx.AsyncClient(timeout=8.0) as http:
            resp = await http.get(
                "https://maps.googleapis.com/maps/api/place/nearbysearch/json",
                params={
                    "location": f"{lat},{lon}",
                    "radius": radio,
                    "keyword": _KEYWORD_BUSQUEDA,
                    "key": api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError:
        logger.exception("Fallo al consultar Google Places API")
        return []

    resultados = []
    for lugar in data.get("results", [])[:5]:
        resultados.append(
            {
                "nombre": lugar.get("name"),
                "direccion": lugar.get("vicinity"),
                "rating": lugar.get("rating"),
            }
        )
    return resultados


async def buscar_centros_cercanos(lat: float | None, lon: float | None) -> dict:
    """
    Busca centros de salud mental cercanos a las coordenadas dadas.

    Si no hay coordenadas, usa Cusco Cercado como centro por defecto y amplía
    el radio de búsqueda (la app se lanza en la región Cusco, incluyendo
    provincias con cobertura dispersa de Google Maps).
    """
    tiene_ubicacion_real = lat is not None and lon is not None
    lat_efectivo = lat if lat is not None else CUSCO_CENTRO_LAT
    lon_efectivo = lon if lon is not None else CUSCO_CENTRO_LON
    radio = 8000 if tiene_ubicacion_real else 15000

    resultados = await _buscar_google_places(lat_efectivo, lon_efectivo, radio)

    if not resultados:
        resultados = cargar_directorio_local_csmc()

    return {
        "centros_cercanos": resultados,
        "recursos_garantizados": RECURSOS_NACIONALES_PE,
    }
