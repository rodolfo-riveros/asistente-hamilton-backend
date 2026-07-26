"""
Cliente asíncrono de Supabase (GoTrue + PostgREST) sobre httpx.

Por qué no se usa el SDK oficial `supabase-py`:
- Su cliente por defecto es síncrono y bloquearía el event loop de FastAPI en
  cada request (todos los endpoints de este backend son `async`).
- Arrastra un árbol de dependencias grande (gotrue/postgrest/realtime/storage3)
  para lo poco que necesitamos: auth por email y CRUD de 4 tablas.
`httpx` ya es dependencia del proyecto, así que esto no añade paquetes nuevos.

Todas las llamadas REST usan la SERVICE ROLE KEY, que **ignora RLS**. Esa clave
nunca debe salir del servidor. El aislamiento por usuario se garantiza aquí,
filtrando siempre por `usuario_id` (ver `app/db/repositorio.py`); las políticas
RLS de `supabase/schema.sql` son la segunda línea de defensa por si algún día
se consulta con la clave anónima desde el cliente.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(15.0, connect=5.0)

_client: httpx.AsyncClient | None = None


class SupabaseError(RuntimeError):
    """Error devuelto por Supabase (auth o base de datos)."""

    def __init__(self, mensaje: str, status_code: int = 500) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.status_code = status_code


class SupabaseNoConfigurado(SupabaseError):
    """Faltan SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY en el entorno."""

    def __init__(self) -> None:
        super().__init__(
            "Supabase no está configurado en el servidor "
            "(faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY).",
            status_code=503,
        )


def _exigir_config() -> None:
    if not settings.supabase_habilitado:
        raise SupabaseNoConfigurado()


def get_client() -> httpx.AsyncClient:
    """Cliente httpx compartido (keep-alive) para todas las llamadas a Supabase."""
    global _client
    if _client is None:
        _exigir_config()
        _client = httpx.AsyncClient(base_url=settings.supabase_url.rstrip("/"), timeout=TIMEOUT)
    return _client


async def cerrar_client() -> None:
    """Cierra el cliente compartido (se llama en el shutdown de FastAPI)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _headers_servicio(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def _mensaje_de_error(respuesta: httpx.Response) -> str:
    """Extrae el mensaje más útil del cuerpo de error de GoTrue/PostgREST."""
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return respuesta.text or f"HTTP {respuesta.status_code}"

    if isinstance(cuerpo, dict):
        for clave in ("error_description", "msg", "message", "error", "hint", "details"):
            valor = cuerpo.get(clave)
            if isinstance(valor, str) and valor:
                return valor
    return str(cuerpo)


def _verificar(respuesta: httpx.Response) -> Any:
    if respuesta.is_success:
        if respuesta.status_code == 204 or not respuesta.content:
            return None
        return respuesta.json()

    mensaje = _mensaje_de_error(respuesta)
    logger.warning("Supabase respondió %s: %s", respuesta.status_code, mensaje)
    raise SupabaseError(mensaje, status_code=respuesta.status_code)


# --------------------------------------------------------------------------- #
# Auth (GoTrue)
# --------------------------------------------------------------------------- #


async def auth_signup(email: str, password: str, metadata: dict | None = None) -> dict:
    """Crea el usuario en Supabase Auth. `metadata` va a `raw_user_meta_data`."""
    _exigir_config()
    payload: dict[str, Any] = {"email": email, "password": password}
    if metadata:
        payload["data"] = metadata

    respuesta = await get_client().post(
        "/auth/v1/signup", json=payload, headers=_headers_servicio()
    )
    return _verificar(respuesta) or {}


async def auth_sign_in(email: str, password: str) -> dict:
    """Inicia sesión con email/contraseña y devuelve tokens + usuario."""
    _exigir_config()
    respuesta = await get_client().post(
        "/auth/v1/token",
        params={"grant_type": "password"},
        json={"email": email, "password": password},
        headers=_headers_servicio(),
    )
    return _verificar(respuesta) or {}


async def auth_get_user(access_token: str) -> dict:
    """Valida un access_token contra Supabase y devuelve el usuario dueño."""
    _exigir_config()
    respuesta = await get_client().get(
        "/auth/v1/user",
        headers=_headers_servicio({"Authorization": f"Bearer {access_token}"}),
    )
    return _verificar(respuesta) or {}


# --------------------------------------------------------------------------- #
# Base de datos (PostgREST)
# --------------------------------------------------------------------------- #


async def select(tabla: str, params: dict[str, Any] | None = None) -> list[dict]:
    _exigir_config()
    respuesta = await get_client().get(
        f"/rest/v1/{tabla}", params=params or {}, headers=_headers_servicio()
    )
    return _verificar(respuesta) or []


async def insert(tabla: str, filas: dict | list[dict], upsert: bool = False) -> list[dict]:
    """Inserta (o hace upsert por clave primaria) y devuelve las filas escritas."""
    _exigir_config()
    prefer = "return=representation"
    if upsert:
        prefer += ",resolution=merge-duplicates"

    respuesta = await get_client().post(
        f"/rest/v1/{tabla}",
        json=filas,
        headers=_headers_servicio({"Prefer": prefer}),
    )
    return _verificar(respuesta) or []


async def subir_archivo(bucket: str, ruta: str, contenido: bytes, content_type: str) -> str:
    """
    Sube un binario a Supabase Storage y devuelve su URL pública.

    Se usa `x-upsert` para poder reemplazar sin borrar antes.
    """
    _exigir_config()
    respuesta = await get_client().post(
        f"/storage/v1/object/{bucket}/{ruta}",
        content=contenido,
        headers=_headers_servicio({"Content-Type": content_type, "x-upsert": "true"}),
    )
    _verificar(respuesta)
    return f"{settings.supabase_url.rstrip('/')}/storage/v1/object/public/{bucket}/{ruta}"


async def update(tabla: str, valores: dict, params: dict[str, Any]) -> list[dict]:
    """Actualiza las filas que cumplan `params` (filtros en sintaxis PostgREST)."""
    _exigir_config()
    respuesta = await get_client().patch(
        f"/rest/v1/{tabla}",
        params=params,
        json=valores,
        headers=_headers_servicio({"Prefer": "return=representation"}),
    )
    return _verificar(respuesta) or []


async def delete(tabla: str, params: dict[str, Any]) -> list[dict]:
    """
    Borra las filas que cumplan `params` y devuelve las borradas.

    Devolver la representación permite distinguir "no existía" (lista vacía ->
    404 en el router) de "se borró" sin una consulta previa.
    """
    _exigir_config()
    respuesta = await get_client().request(
        "DELETE",
        f"/rest/v1/{tabla}",
        params=params,
        headers=_headers_servicio({"Prefer": "return=representation"}),
    )
    return _verificar(respuesta) or []


async def contar(tabla: str, params: dict[str, Any] | None = None) -> int:
    """
    Número de filas que cumplen `params`, sin traérselas.

    Usa `Prefer: count=exact` de PostgREST y lee el total de la cabecera
    `Content-Range` (formato `0-0/42`). Se pide un rango de una sola fila para
    que la base cuente pero no serialice nada.
    """
    _exigir_config()
    consulta = {**(params or {}), "select": "*"}
    respuesta = await get_client().get(
        f"/rest/v1/{tabla}",
        params=consulta,
        headers=_headers_servicio({"Prefer": "count=exact", "Range": "0-0"}),
    )
    if not respuesta.is_success:
        _verificar(respuesta)  # levanta SupabaseError con el mensaje del cuerpo

    rango = respuesta.headers.get("content-range", "")
    total = rango.split("/")[-1] if "/" in rango else ""
    try:
        return int(total)
    except ValueError:
        logger.warning("Supabase devolvió un Content-Range inesperado: %r", rango)
        return 0
