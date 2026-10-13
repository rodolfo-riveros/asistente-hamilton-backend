"""Cabeceras de servicio con claves heredadas (JWT) y nuevas (`sb_secret_…`)."""

from app.config import settings
from app.db import supabase


def test_clave_nueva_va_solo_en_apikey(monkeypatch):
    monkeypatch.setattr(settings, "supabase_service_role_key", "sb_secret_abc123")
    h = supabase._headers_servicio()
    assert h["apikey"] == "sb_secret_abc123"
    assert "Authorization" not in h


def test_clave_jwt_tambien_como_bearer(monkeypatch):
    monkeypatch.setattr(settings, "supabase_service_role_key", "eyJhbGciOi.eyJyb2xlIjoi.c2lnbmF0dXJl")
    h = supabase._headers_servicio()
    assert h["Authorization"] == "Bearer eyJhbGciOi.eyJyb2xlIjoi.c2lnbmF0dXJl"


def test_token_de_usuario_sustituye_authorization(monkeypatch):
    monkeypatch.setattr(settings, "supabase_service_role_key", "sb_secret_abc123")
    h = supabase._headers_servicio({"Authorization": "Bearer token-de-usuario"})
    assert h["Authorization"] == "Bearer token-de-usuario"
    assert h["apikey"] == "sb_secret_abc123"


def test_supabase_inalcanzable_da_503_no_500(monkeypatch):
    """Proyecto borrado o sin red: la API responde 503 con detalle, no 500."""
    import httpx
    from fastapi.testclient import TestClient

    from app.main import app

    async def caer(*_a, **_k):
        raise httpx.ConnectError("nombre no resuelto")

    monkeypatch.setattr(settings, "supabase_url", "https://proyecto-borrado.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_role_key", "sb_secret_x")
    monkeypatch.setattr(httpx.AsyncClient, "post", caer)
    r = TestClient(app).post("/auth/login", json={"email": "a@b.co", "password": "x123456"})
    assert r.status_code == 503
    assert "no está disponible" in r.json()["detail"]
