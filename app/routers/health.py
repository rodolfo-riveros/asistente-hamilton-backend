"""Router de monitoreo: GET /salud."""

from fastapi import APIRouter

from app.config import settings

router = APIRouter(tags=["salud"])


@router.get("/salud")
async def salud() -> dict:
    """
    Estado del servicio y sus integraciones.

    Sirve para ver de un vistazo qué IA está activa y si falta alguna
    credencial, sin exponer ninguna clave.
    """
    return {
        "status": "ok",
        "llm": {
            "proveedor": settings.llm_provider,
            "modelo": settings.llm_model or "(preset del proveedor)",
            "credencial": bool(settings.llm_api_key or settings.anthropic_api_key),
        },
        "supabase": settings.supabase_habilitado,
        "voz": settings.stt_habilitado,
    }
