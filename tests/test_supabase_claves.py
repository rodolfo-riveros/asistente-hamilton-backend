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
