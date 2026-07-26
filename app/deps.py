"""
Dependencias compartidas de FastAPI: identidad del usuario a partir del
access_token de Supabase Auth que envía la app en `Authorization: Bearer ...`.

El token se valida contra Supabase (`GET /auth/v1/user`) en vez de verificar la
firma localmente: así una sesión revocada deja de funcionar de inmediato y no
hay que sincronizar el JWT secret del proyecto.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db import supabase

logger = logging.getLogger(__name__)

# auto_error=False: los endpoints deciden si el token es obligatorio.
bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Usuario:
    id: str
    email: str | None
    nombre: str


def _a_usuario(datos: dict) -> Usuario:
    metadata = datos.get("user_metadata") or {}
    email = datos.get("email")
    nombre = metadata.get("nombre") or metadata.get("full_name") or (email or "").split("@")[0]
    return Usuario(id=datos["id"], email=email, nombre=nombre)


async def usuario_opcional(
    credenciales: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> Usuario | None:
    """Devuelve el usuario si vino un token válido; None si no hay token."""
    if credenciales is None or not credenciales.credentials:
        return None

    try:
        datos = await supabase.auth_get_user(credenciales.credentials)
    except supabase.SupabaseNoConfigurado:
        # Sin Supabase configurado no hay identidad posible: el chat sigue
        # funcionando de forma anónima (útil en desarrollo local).
        return None
    except supabase.SupabaseError:
        logger.info("Token de acceso inválido o expirado")
        return None

    if not datos.get("id"):
        return None
    return _a_usuario(datos)


async def usuario_actual(usuario: Usuario | None = Depends(usuario_opcional)) -> Usuario:
    """Igual que `usuario_opcional`, pero exige sesión válida."""
    if usuario is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Necesitas iniciar sesión para acceder a este recurso.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return usuario
