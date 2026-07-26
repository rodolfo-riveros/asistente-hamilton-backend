"""
Punto de entrada de la aplicación FastAPI "Asistente Hamilton".

Corre con:
    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import supabase
from app.llm import factory as llm_factory
from app.routers import auth, chat, evaluaciones, health, voz
from app.voz import stt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if not settings.supabase_habilitado:
        logger.warning(
            "Supabase no está configurado: /auth y /evaluaciones responderán 503 y "
            "las sesiones vivirán sólo en memoria. Define SUPABASE_URL y "
            "SUPABASE_SERVICE_ROLE_KEY en el .env."
        )
    if not settings.stt_habilitado:
        logger.info("Voz: transcripción desactivada (define STT_BASE_URL para habilitarla).")

    yield

    # Cierra los pools de conexiones abiertos.
    await supabase.cerrar_client()
    await llm_factory.cerrar_proveedor()
    await stt.cerrar_cliente()


app = FastAPI(
    lifespan=lifespan,
    title="Asistente Hamilton",
    description=(
        "Backend conversacional que reemplaza el formulario de Streamlit del "
        "test de depresión de Hamilton por un asistente con Claude (Anthropic) "
        "y un modelo SVM entrenado para clasificar el nivel de depresión."
    ),
    version="0.1.0",
)

# CORS: la app en React Native (Expo en dev, dominio propio en prod) necesita
# poder llamar a esta API. Restringir ALLOWED_ORIGINS en el .env de producción.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=settings.allowed_origins_list != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(evaluaciones.router)
app.include_router(voz.router)

# Puntos de extensión previstos (ver README):
#   - POST /chat/iniciar   -> mensaje de bienvenida sin esperar input del usuario
#   - POST /reportes/pdf   -> reconstruir el PDF clínico que generaba el Streamlit
#     original con reportlab, a partir del resultado guardado de una sesión.
