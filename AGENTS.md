# Asistente Hamilton — Backend

## Stack

- **FastAPI** con `uvicorn` (dev: `--reload`)
- **pydantic-settings** carga `.env` automáticamente
- **scikit-learn 1.6.1** — PINNED. Modelos `.pkl` serializados con esa versión.
  No subir la versión sin reentrenar y re-verificar el pipeline.
- **httpx** para Supabase, STT, Google Places y LLMs. No hay `requests`, no hay `supabase-py`.
- **pytest** (asyncio_mode=auto, pythonpath=.)

## Comandos

| Comando | Uso |
|---|---|
| `uvicorn app.main:app --reload` | Servidor dev en `http://127.0.0.1:8000` |
| `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000` | Accesible desde la red local |
| `pytest` | Tests. Sin args corre todos. |
| `python scripts/verificar_supabase.py` | Valida credenciales y schema de Supabase |
| `python scripts/actualizar_directorio_csmc.py` | Actualiza el directorio local de centros |

## Arquitectura

- **`app/main.py`** — entrypoint, CORS, lifespan, incluye routers
- **`app/config.py`** — `Settings` singleton vía `pydantic-settings` desde `.env`
- **`app/llm/factory.py`** — selecciona proveedor según `LLM_PROVIDER`. Presets en `PRESETS`.
   Usa `OpenAICompatProvider` para todo excepto `anthropic` (SDK oficial).
   **Cadena de respaldo**: si `LLM_FALLBACK_API_KEY` está definida, `crear_proveedor()` devuelve
   un `FallbackProvider` que prueba primario → fallback → terciario en orden hasta que uno responda.
- **`app/chat/orchestrator.py`** — loop de tool-use (máx. 8 iteraciones, 1024 tokens).
  Historial en formato neutro JSON-serializable.
- **`app/chat/session_store.py`** — `MemorySessionStore` (dev) o `SupabaseSessionStore` (si hay credenciales).
  Sesiones se pierden al reiniciar sin Supabase. Interfaz async.
- **`app/db/supabase.py`** — cliente async httpx contra GoTrue + PostgREST.
  Usa `SUPABASE_SERVICE_ROLE_KEY` (ignora RLS). Aislamiento por `usuario_id` en `repositorio.py`.
- **`app/db/repositorio.py`** — escrituras best-effort (no tiran la conversación si fallan).
- **`app/prompts/system_prompt.py`** — texto clínico, NO ALTERAR sin el equipo de psicología.
- **`app/ml/modelo.py`** — pipeline SVM. NO ALTERAR, sustenta la tesis.
  `calcular_resultado()` exige exactamente 17 enteros.
- **`app/safety/risk_filter.py`** — filtro determinístico complementario. No reemplaza al LLM.
  Nunca fuerza respuesta automática. No registra el texto completo del mensaje.
- **`app/chat/resultado.py`** — traduce salida del SVM al contrato de la app (`{score, nivel, interpretacion, recomendaciones, fecha, sesion_id}`).
- **`app/locations/centros.py`** — 2 niveles: Google Places API (si hay key) → directorio local.
- **`app/voz/stt.py`** — solo STT. Contrato OpenAI `/audio/transcriptions`. TTS lo hace el teléfono con `expo-speech`.

## APIs externas

- **LLM**: obligatorio. El modelo debe soportar tool-calling.
- **Supabase**: opcional. Sin ella → sesiones en memoria, `/auth/*` y `/evaluaciones` devuelven 503.
- **Google Places**: opcional. Sin ella → solo directorio local `app/locations/data/csmc_cusco.json`.
- **STT**: opcional. Sin `STT_BASE_URL` → `POST /voz/transcribir` devuelve 503.

## Reglas críticas

- `scikit-learn` fijado a **1.6.1**. No tocar sin reentrenar modelo.
- `SUPABASE_SERVICE_ROLE_KEY` solo en el `.env` del servidor. Jamás en el repo ni en la app móvil.
- No se registra el texto completo de mensajes de crisis en logs — solo el evento (timestamp, sesión, flag).
- CORS: `ALLOWED_ORIGINS=*` en dev. Restringir en producción.
- Si el modelo `.pkl` falla al cargar → `ModeloNoDisponibleError` → 503 con mensaje claro.
- Si el LLM falla → el orquestador devuelve un mensaje conversacional de error, no deja el chat colgado.

## Tests

- `pytest` sin args corre todo. `asyncio_mode = auto` → los `async` tests funcionan sin decorador.
- `tests/test_modelo.py` — carga el `.pkl` real (necesita `SVM_MODELO.pkl` y `SVM_scaler.pkl` en `app/ml/models/`).
- `tests/test_llm_adaptadores.py` — sin red, solo conversión de estructuras.

## Endpoints (todos los routers en `app/routers/`)

| Ruta | Auth | Notas |
|---|---|---|
| `GET /salud` | No | Estado del servicio (no expone claves) |
| `POST /auth/register` | No | `{nombre, email, password}`. Si Supabase tiene "Confirm email" → 202 sin token |
| `POST /auth/login` | No | `{email, password}` |
| `GET /auth/me` | Sí | Datos del usuario autenticado |
| `POST /chat/mensaje` | Opcional | Token opcional → sesión anónima. Admite `sesion_id`, `lat`, `lon` |
| `GET /evaluaciones` | Sí | Histórico del usuario, más reciente primero |
| `POST /voz/transcribir` | Opcional | Multipart con `audio`. 25 MB máx |
