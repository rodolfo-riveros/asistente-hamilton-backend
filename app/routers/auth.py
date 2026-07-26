"""
Router de autenticación: /auth/register, /auth/login, /auth/me.

La identidad la maneja Supabase Auth (GoTrue): hashing de contraseñas, tokens y
recuperación de cuenta corren por su cuenta. Este backend sólo hace de fachada
para que la app móvil hable con un único origen, y mantiene la tabla
`public.perfiles` sincronizada con el nombre visible del usuario.

El contrato de respuesta es el que ya esperaba la app:
    {"token": "<access_token>", "user": {"id", "nombre", "email"}}

Nota de configuración: si en Supabase está activada la confirmación por correo
("Confirm email"), el registro NO devuelve sesión y el usuario debe confirmar
antes de entrar. Para el flujo móvil de esta app se recomienda desactivarla
(Dashboard -> Authentication -> Providers -> Email -> Confirm email = off).
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.db import repositorio, supabase
from app.deps import Usuario, usuario_actual

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Validación de forma, no de existencia: quien valida de verdad el correo es
# Supabase. Se usa un regex simple para no añadir la dependencia email-validator.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validar_email(valor: str) -> str:
    valor = valor.strip().lower()
    if not EMAIL_RE.match(valor):
        raise ValueError("El correo electrónico no tiene un formato válido.")
    return valor


class RegistroRequest(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=6, max_length=128)

    _normaliza_email = field_validator("email")(_validar_email)


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=1, max_length=128)

    _normaliza_email = field_validator("email")(_validar_email)


class UsuarioResponse(BaseModel):
    id: str
    nombre: str
    email: str | None = None


class AuthResponse(BaseModel):
    token: str
    user: UsuarioResponse


def _error_http(exc: supabase.SupabaseError) -> HTTPException:
    """Traduce un error de Supabase a HTTPException sin filtrar detalles internos."""
    if isinstance(exc, supabase.SupabaseNoConfigurado):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.mensaje)
    if exc.status_code in (400, 401, 403, 422):
        return HTTPException(status_code=exc.status_code, detail=exc.mensaje)
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="El servicio de cuentas no está disponible en este momento.",
    )


def _extraer_usuario(datos: dict, nombre_por_defecto: str = "") -> UsuarioResponse:
    usuario = datos.get("user") or datos
    metadata = usuario.get("user_metadata") or {}
    email = usuario.get("email")
    nombre = (
        metadata.get("nombre")
        or nombre_por_defecto
        or metadata.get("full_name")
        or (email or "").split("@")[0]
    )
    return UsuarioResponse(id=usuario["id"], nombre=nombre, email=email)


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def registrar(payload: RegistroRequest) -> AuthResponse:
    try:
        datos = await supabase.auth_signup(
            email=payload.email,
            password=payload.password,
            metadata={"nombre": payload.nombre},
        )
    except supabase.SupabaseError as exc:
        raise _error_http(exc) from exc

    usuario = _extraer_usuario(datos, nombre_por_defecto=payload.nombre)
    token = datos.get("access_token")

    if not token:
        # Supabase creó la cuenta pero exige confirmar el correo antes de emitir
        # sesión: se lo decimos al usuario en vez de devolver un token vacío.
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail=(
                "Cuenta creada. Revisa tu correo para confirmarla y luego inicia sesión."
            ),
        )

    # El perfil es "de mejor esfuerzo": si falla, la cuenta ya existe y el
    # usuario puede entrar igual.
    try:
        await repositorio.upsert_perfil(usuario.id, usuario.nombre, usuario.email)
    except supabase.SupabaseError:
        logger.exception("No se pudo crear el perfil del usuario %s", usuario.id)

    return AuthResponse(token=token, user=usuario)


@router.post("/login", response_model=AuthResponse)
async def iniciar_sesion(payload: LoginRequest) -> AuthResponse:
    try:
        datos = await supabase.auth_sign_in(payload.email, payload.password)
    except supabase.SupabaseError as exc:
        if exc.status_code in (400, 401):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Correo o contraseña incorrectos.",
            ) from exc
        raise _error_http(exc) from exc

    token = datos.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos.",
        )

    usuario = _extraer_usuario(datos)

    # Mantiene el perfil al día si el usuario se registró antes de existir la tabla.
    try:
        await repositorio.upsert_perfil(usuario.id, usuario.nombre, usuario.email)
    except supabase.SupabaseError:
        logger.exception("No se pudo sincronizar el perfil del usuario %s", usuario.id)

    return AuthResponse(token=token, user=usuario)


@router.get("/me", response_model=UsuarioResponse)
async def yo(usuario: Usuario = Depends(usuario_actual)) -> UsuarioResponse:
    return UsuarioResponse(id=usuario.id, nombre=usuario.nombre, email=usuario.email)
