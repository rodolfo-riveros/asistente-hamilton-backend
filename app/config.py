"""
Configuración de la aplicación vía variables de entorno.

Usa pydantic-settings para que todo el estado de configuración pase por un
único objeto (`settings`), fácil de mockear en tests y de extender a medida
que se agreguen más integraciones (Redis, etc.).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Proveedor de texto ---
    # deepseek | nvidia | anthropic | openai | groq | openrouter | ollama | openai_compat
    # base_url y model quedan vacíos para usar el preset del proveedor
    # (ver app/llm/factory.py).
    llm_provider: str = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""

    # --- Fallback / cadena de respaldo ---
    # Se intentan en orden si el primario falla.
    llm_fallback_api_key: str = ""
    llm_fallback_model: str = ""
    llm_fallback_base_url: str = ""
    llm_tertiary_api_key: str = ""
    llm_tertiary_model: str = ""
    llm_tertiary_base_url: str = ""

    # --- Anthropic (sólo si LLM_PROVIDER=anthropic) ---
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # --- Voz: transcripción (STT) ---
    # Endpoint compatible con OpenAI /audio/transcriptions.
    stt_base_url: str = ""
    stt_api_key: str = ""
    stt_model: str = "whisper-1"
    stt_idioma: str = "es"

    # --- Google Places (opcional) ---
    google_places_api_key: str | None = None

    # --- Supabase ---
    # SUPABASE_URL: https://<project-ref>.supabase.co
    # SUPABASE_SERVICE_ROLE_KEY: clave de servicio (ignora RLS). NUNCA exponerla
    # al cliente móvil ni commitearla; sólo vive en el .env del servidor.
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # --- CORS ---
    # Lista separada por comas en el .env, ej: "http://localhost:19006,https://miapp.com"
    # "*" habilita cualquier origen (recomendado solo en dev/Expo).
    allowed_origins: str = "*"

    # --- Entorno ---
    environment: str = "development"

    # --- Rutas de modelo ML (no se esperan en .env; son fijas al repo) ---
    modelo_path: Path = BASE_DIR / "app" / "ml" / "models" / "SVM_MODELO.pkl"
    scaler_path: Path = BASE_DIR / "app" / "ml" / "models" / "SVM_scaler.pkl"

    # --- Directorio local de centros de salud mental ---
    csmc_json_path: Path = BASE_DIR / "app" / "locations" / "data" / "csmc_cusco.json"

    @property
    def allowed_origins_list(self) -> list[str]:
        if self.allowed_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def stt_habilitado(self) -> bool:
        """True si hay un endpoint de transcripción configurado."""
        return bool(self.stt_base_url.strip())

    @property
    def supabase_habilitado(self) -> bool:
        """True si hay credenciales de Supabase; si no, el backend corre sin persistencia."""
        return bool(self.supabase_url.strip() and self.supabase_service_role_key.strip())

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"prod", "production"}


@lru_cache
def get_settings() -> Settings:
    """Cachea la instancia de Settings (se lee una sola vez el .env)."""
    return Settings()


settings = get_settings()
