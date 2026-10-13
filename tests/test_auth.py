"""
Tests del contrato de auth con la app (sin red: se parchea `app.db.supabase`).

Cubre lo que la app necesita para no dejar a la persona colgada:
- `refresh_token` en login y `POST /auth/refresh` para renovar sin volver al login.
- Mensajes de GoTrue traducidos en `detail`.
- Un Supabase caído NO se confunde con un token inválido (503, no 401).
- Un `sesion_id` ajeno no se puede usar en el chat.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import repositorio, supabase
from app.main import app

SESION_SUPABASE = {
    "access_token": "acc-1",
    "refresh_token": "ref-1",
    "expires_in": 3600,
    "user": {"id": "u-1", "email": "ana@example.com", "user_metadata": {"nombre": "Ana"}},
}


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "https://x.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_role_key", "service")

    async def upsert_perfil(*_a, **_k):
        return None

    monkeypatch.setattr(repositorio, "upsert_perfil", upsert_perfil)
    return TestClient(app)


def test_login_devuelve_refresh_token(monkeypatch, cliente):
    async def sign_in(email, password):
        return SESION_SUPABASE

    monkeypatch.setattr(supabase, "auth_sign_in", sign_in)
    resp = cliente.post("/auth/login", json={"email": "ana@example.com", "password": "secreta"})

    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["token"] == "acc-1"
    assert cuerpo["refresh_token"] == "ref-1"
    assert cuerpo["expires_in"] == 3600
    assert cuerpo["user"] == {"id": "u-1", "nombre": "Ana", "email": "ana@example.com"}


def test_login_incorrecto_es_401_con_detail(monkeypatch, cliente):
    async def sign_in(email, password):
        raise supabase.SupabaseError("Invalid login credentials", status_code=400)

    monkeypatch.setattr(supabase, "auth_sign_in", sign_in)
    resp = cliente.post("/auth/login", json={"email": "ana@example.com", "password": "mala"})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Correo o contraseña incorrectos."


def test_login_sin_confirmar_correo_lo_dice(monkeypatch, cliente):
    async def sign_in(email, password):
        raise supabase.SupabaseError("Email not confirmed", status_code=400)

    monkeypatch.setattr(supabase, "auth_sign_in", sign_in)
    resp = cliente.post("/auth/login", json={"email": "ana@example.com", "password": "secreta"})

    assert resp.status_code == 403
    assert "Confirma tu correo" in resp.json()["detail"]


def test_registro_duplicado_en_espanol(monkeypatch, cliente):
    async def signup(**_k):
        raise supabase.SupabaseError("User already registered", status_code=422)

    monkeypatch.setattr(supabase, "auth_signup", signup)
    resp = cliente.post(
        "/auth/register",
        json={"nombre": "Ana", "email": "ana@example.com", "password": "secreta"},
    )

    assert resp.status_code == 422
    assert resp.json()["detail"] == "Ya existe una cuenta con ese correo. Inicia sesión."


def test_refresh_renueva_la_sesion(monkeypatch, cliente):
    async def refresh(token):
        assert token == "ref-1"
        return {**SESION_SUPABASE, "access_token": "acc-2", "refresh_token": "ref-2"}

    monkeypatch.setattr(supabase, "auth_refresh", refresh)
    resp = cliente.post("/auth/refresh", json={"refresh_token": "ref-1"})

    assert resp.status_code == 200
    assert resp.json()["token"] == "acc-2"
    assert resp.json()["refresh_token"] == "ref-2"


def test_refresh_caducado_es_401(monkeypatch, cliente):
    async def refresh(token):
        raise supabase.SupabaseError("Invalid Refresh Token", status_code=400)

    monkeypatch.setattr(supabase, "auth_refresh", refresh)
    resp = cliente.post("/auth/refresh", json={"refresh_token": "viejo"})

    assert resp.status_code == 401
    assert "Vuelve a iniciar sesión" in resp.json()["detail"]


def test_token_invalido_es_401(monkeypatch, cliente):
    async def get_user(token):
        raise supabase.SupabaseError("invalid JWT", status_code=401)

    monkeypatch.setattr(supabase, "auth_get_user", get_user)
    resp = cliente.get("/auth/me", headers={"Authorization": "Bearer caducado"})
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "fallo",
    [supabase.SupabaseError("upstream", status_code=502), httpx.ConnectError("sin red")],
)
def test_supabase_caido_no_cierra_la_sesion(monkeypatch, cliente, fallo):
    async def get_user(token):
        raise fallo

    monkeypatch.setattr(supabase, "auth_get_user", get_user)
    resp = cliente.get("/auth/me", headers={"Authorization": "Bearer bueno"})

    assert resp.status_code == 503
    assert isinstance(resp.json()["detail"], str)


# ------------------------------------------------------------ sesion_id ajeno


@pytest.mark.parametrize(
    "filas,usuario,esperado",
    [
        ([], "u-1", True),  # no existe: se creará
        ([{"usuario_id": None}], "u-1", True),  # anónima: se adopta
        ([{"usuario_id": "u-1"}], "u-1", True),  # propia
        ([{"usuario_id": "u-2"}], "u-1", False),  # ajena
        ([{"usuario_id": "u-2"}], None, False),  # ajena, sin sesión
    ],
)
async def test_sesion_utilizable(monkeypatch, filas, usuario, esperado):
    async def select(tabla, params=None):
        return filas

    monkeypatch.setattr(supabase, "select", select)
    sid = "4f1c2b8e-0d3a-4a7e-9f1e-2b3c4d5e6f70"
    assert await repositorio.sesion_utilizable(sid, usuario) is esperado


async def test_sesion_id_mal_formado_no_se_usa(monkeypatch):
    async def select(tabla, params=None):  # pragma: no cover - no debe llamarse
        raise AssertionError("no debería consultar Supabase")

    monkeypatch.setattr(supabase, "select", select)
    assert await repositorio.sesion_utilizable("no-es-uuid", "u-1") is False


# ------------------------------------------------------------------ PUT /perfil


def test_put_perfil_solo_envia_lo_que_llego_y_vacio_borra(monkeypatch, cliente):
    from app.deps import Usuario, usuario_actual

    recibido: dict = {}

    async def actualizar(usuario_id, campos, permitir_nulos=False):
        recibido.update(campos=campos, permitir_nulos=permitir_nulos)
        return {"id": usuario_id, "nombre": "Ana", "ciudad": None}

    monkeypatch.setattr(repositorio, "actualizar_perfil", actualizar)
    app.dependency_overrides[usuario_actual] = lambda: Usuario(id="u-1", email=None, nombre="Ana")
    try:
        resp = cliente.put("/perfil", json={"ciudad": "", "bio": None, "nombre": ""})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    # `edad`/`ocupacion` no se mandaron: no se tocan. El nombre vacío se ignora.
    assert recibido == {"campos": {"ciudad": None, "bio": None}, "permitir_nulos": True}
