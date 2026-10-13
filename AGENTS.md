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
- **`app/chat/orchestrator.py`** — loop de tool-use (máx. 8 iteraciones, 2048 tokens).
  Historial en formato neutro JSON-serializable. La ubicación del turno va en el system
  prompt (bloque `UBICACIÓN`), nunca en el texto del usuario. Acumula `acciones` del turno.
  v5: también añade los bloques `ESTADO DE LA PERSONA` (modo seguimiento) y `FECHA Y HORA`
  (Perú, UTC-5) y devuelve `opciones` (la última llamada válida a `ofrecer_opciones`).
- **`app/chat/seguimiento.py`** — modo seguimiento/recuperación calculado en el backend con
  las evaluaciones guardadas: seguimiento si el último nivel es "Sin depresión" o la tendencia
  mejora claramente (baja ≥1 nivel o ≥6 puntos HDRS) sin ser "Depresión severa". Umbrales en
  constantes del módulo. Best-effort: si la BD falla no se añade bloque.
- **`app/chat/acciones.py`** — botones del chat (contrato v4): `musica`, `recurso`, `centro`,
  `buscar_mapa`, `llamar`, `actividad`, y `contacto_confianza` (v5, en crisis junto a 113/106;
  la app lo resuelve con la persona de confianza del teléfono, nunca llama sola).
  `normalizar_opciones` valida las respuestas rápidas (2–5, ≤32 caracteres, sin duplicados).
  `RECURSOS_APP` = ids de `src/screens/recursos/contenido.ts`
  de la app; si cambian allí, cambiar aquí.
- **`app/chat/session_store.py`** — `MemorySessionStore` (dev) o `SupabaseSessionStore` (si hay credenciales).
  Sesiones se pierden al reiniciar sin Supabase. Interfaz async.
- **`app/db/supabase.py`** — cliente async httpx contra GoTrue + PostgREST.
  Usa `SUPABASE_SERVICE_ROLE_KEY` (ignora RLS). Aislamiento por `usuario_id` en `repositorio.py`.
- **`app/db/repositorio.py`** — escrituras best-effort (no tiran la conversación si fallan).
- **`app/prompts/system_prompt.py`** — tono "amiga que escucha" + test muy espaciado (v4).
  Los 17 ítems, sus escalas y la PARTE 2 (crisis) NO SE ALTERAN sin el equipo de psicología.
- **`app/ml/modelo.py`** — pipeline SVM. NO ALTERAR, sustenta la tesis.
  `calcular_resultado()` exige exactamente 17 enteros.
- **`app/safety/risk_filter.py`** — filtro determinístico complementario. No reemplaza al LLM.
  Nunca fuerza respuesta automática. No registra el texto completo del mensaje.
- **`app/chat/resultado.py`** — traduce salida del SVM al contrato de la app (`{score, nivel, interpretacion, recomendaciones, fecha, sesion_id}`).
- **`app/locations/centros.py`** — 2 niveles: Google Places API (si hay key) → directorio local.
- **`app/voz/stt.py`** — STT. Contrato OpenAI `/audio/transcriptions`.
- **`app/voz/tts.py`** — TTS opcional con Azure AI Speech (SSML calmado, MP3). Por defecto la app habla con `expo-speech`; sólo usa esto con `EXPO_PUBLIC_TTS_NUBE=true` y cae al teléfono si falla.

## APIs externas

- **LLM**: obligatorio. El modelo debe soportar tool-calling.
- **Supabase**: opcional. Sin ella → sesiones en memoria, `/auth/*` y `/evaluaciones` devuelven 503.
- **Google Places**: opcional. Sin ella → solo directorio local `app/locations/data/csmc_cusco.json`.
- **STT**: opcional. Sin `STT_BASE_URL` → `POST /voz/transcribir` devuelve 503.
- **Azure AI Speech (TTS)**: opcional. Sin `AZURE_SPEECH_KEY`/`AZURE_SPEECH_REGION` → `POST /voz/sintetizar` devuelve 503.

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
- `tests/test_acciones.py` (v4) y `tests/test_chat_v5.py` (opciones, modo seguimiento,
  `contacto_confianza`) — sin red: proveedor con guion y `memoria_de`/`listar_evaluaciones` parcheados.

## Endpoints (todos los routers en `app/routers/`)

| Ruta | Auth | Notas |
|---|---|---|
| `GET /salud` | No | Estado del servicio (no expone claves) |
| `POST /auth/register` | No | `{nombre, email, password}`. Si Supabase tiene "Confirm email" → 202 sin token |
| `POST /auth/login` | No | `{email, password}` → `{token, user, refresh_token, expires_in}` |
| `POST /auth/refresh` | No | `{refresh_token}` → igual que login. 401 si caducó |
| `GET /auth/me` | Sí | Datos del usuario autenticado |
| `POST /chat/mensaje` | Opcional | Token opcional → sesión anónima. Admite `sesion_id`, `lat`, `lon`. Devuelve siempre `acciones: Accion[]` y `opciones: string[]` (respuestas rápidas; no se guardan) |
| `GET /chat/historial` | Sí | Hilo completo; cada mensaje con `acciones` (`null` → `[]`) |
| `GET /evaluaciones` | Sí | Histórico del usuario, más reciente primero |
| `POST /voz/transcribir` | Opcional | Multipart con `audio`. 25 MB máx |
| `POST /voz/sintetizar` | Sí | `{texto, voz?, ritmo?}` → `audio/mpeg`. ≤1500 caracteres. 503 sin Azure |

## Migraciones

`supabase/schema.sql` → `schema_v2.sql` → `schema_v3.sql` → `schema_v4.sql` (`mensajes.acciones jsonb`).
`supabase/instalar_todo.sql` las junta todas (proyecto nuevo). Todas idempotentes.
El backend tolera que v4 aún no esté aplicada: reintenta sin `acciones` y avisa una vez en el log.
