# Asistente Hamilton — Backend

Backend en **FastAPI** que reemplaza el formulario de Streamlit del test de
depresión de Hamilton por un **asistente conversacional** (Claude, vía tool
use) que:

- Recopila los 17 ítems de la Escala de Hamilton charlando de forma natural.
- Corre el modelo **SVM ya entrenado** (98.70% de exactitud) para clasificar
  el nivel de depresión — el LLM nunca diagnostica por su cuenta.
- Activa un **protocolo de crisis** si detecta señales de riesgo suicida en
  cualquier momento de la charla, con recursos de ayuda garantizados
  (Línea 113 opción 5, SAMU 106) y búsqueda opcional de centros de salud
  mental cercanos.
- Expone la API con CORS abierto para ser consumida por una app en React
  Native.

> La lógica de inferencia ML (`app/ml/modelo.py`) preserva **exactamente** el
> pipeline del Streamlit original. No se debe alterar sin que el equipo de la
> tesis lo revise — es lo que sustenta los resultados académicos.

---

## 1. Requisitos

- Python 3.11+ (probado con 3.12)
- Una API key de Anthropic (obligatoria para que el asistente converse)
- Opcional: una API key de Google Places (si falta, la búsqueda de centros
  usa solo el directorio local `app/locations/data/csmc_cusco.json`)

---

## 2. Instalación

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Variables de entorno

```bash
cp .env.example .env
```

Edita `.env` y completa al menos la clave del proveedor de IA y las de Supabase:

```
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-...

GOOGLE_PLACES_API_KEY=            # opcional
ALLOWED_ORIGINS=*                 # restringir en producción
ENVIRONMENT=development

SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ...  # clave service_role / secret
```

> **`SUPABASE_SERVICE_ROLE_KEY` ignora RLS.** Sólo va en el `.env` del
> servidor: nunca en la app móvil ni en el repositorio.

Sin las variables de Supabase el backend arranca igual, pero en modo degradado:
`/auth/*` y `/evaluaciones` responden `503` y las sesiones viven sólo en memoria
del proceso.

`GET /salud` te dice de un vistazo qué está activo, sin exponer claves:

```json
{
  "status": "ok",
  "llm": { "proveedor": "deepseek", "modelo": "(preset del proveedor)", "credencial": true },
  "supabase": true,
  "voz": false
}
```

Los archivos `SVM_MODELO.pkl` y `SVM_scaler.pkl` ya están incluidos en
`app/ml/models/` (copiados del Streamlit original) — no hace falta moverlos
ni reentrenarlos.

### Cambiar de proveedor de IA

Todo el backend habla con la IA a través de `app/llm/`, así que cambiar de
modelo es cambiar dos variables. No hay nada específico de un proveedor en el
orquestador ni en el estado de las sesiones.

| `LLM_PROVIDER` | Preset de URL | Modelo por defecto |
| --- | --- | --- |
| `deepseek` | `https://api.deepseek.com/v1` | `deepseek-v4-flash` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-2.5-flash` |
| `nvidia` | `https://integrate.api.nvidia.com/v1` | `meta/llama-3.3-70b-instruct` |
| `openai` | `https://api.openai.com/v1` | `gpt-4o-mini` |
| `groq` | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |
| `openrouter` | `https://openrouter.ai/api/v1` | `meta-llama/llama-3.3-70b-instruct` |
| `ollama` | `http://localhost:11434/v1` | `llama3.1` |
| `anthropic` | SDK oficial | `ANTHROPIC_MODEL` |
| `openai_compat` | define `LLM_BASE_URL` | define `LLM_MODEL` |

`LLM_BASE_URL` y `LLM_MODEL` sobreescriben el preset. Para cualquier API
compatible con OpenAI que no esté en la tabla, usa `openai_compat`.

> **El modelo que elijas debe soportar tool-calling.** El asistente registra
> cada respuesta del test y dispara el cálculo del SVM mediante tools; un modelo
> que no las llame mantendrá la conversación pero nunca cerrará la evaluación.
>
> Comprobado el 26/07/2026: `deepseek-v4-flash`, `deepseek-v4-pro`,
> `gemini-2.5-flash` y `meta/llama-3.3-70b-instruct` llaman a las tools sin
> problemas. `openai/gpt-oss-20b` de NVIDIA **no** lo hace de forma fiable.

### Cadena de respaldo

Si el primario falla (cuota, caída, timeout) se prueba el siguiente:

```
LLM_PROVIDER=deepseek            LLM_API_KEY=...
LLM_FALLBACK_PROVIDER=gemini     LLM_FALLBACK_API_KEY=...
LLM_TERTIARY_PROVIDER=nvidia     LLM_TERTIARY_API_KEY=...
```

### Voz

- **Escuchar (STT)**: `POST /voz/transcribir` recibe el audio y devuelve texto.
  Habla el contrato de OpenAI `/audio/transcriptions`, así que sirve cualquier
  servicio compatible (Whisper de OpenAI, Groq, faster-whisper local, endpoints
  de NVIDIA que lo implementen). Se configura con `STT_BASE_URL`, `STT_API_KEY`,
  `STT_MODEL` y `STT_IDIOMA`. Vacío = dictado desactivado (`503`).
- **Hablar (TTS)**: por defecto no pasa por el backend: lo sintetiza el propio
  teléfono con `expo-speech` (gratis, sin red, sin cuota).
- **Hablar con voz neuronal (opcional)**: `POST /voz/sintetizar` (Bearer
  obligatorio) recibe `{"texto": "...", "voz"?: "es-PE-CamilaNeural",
  "ritmo"?: "pausada" | "normal"}` y devuelve `200 audio/mpeg` (MP3 mono 24 kHz
  48 kbps) generado con **Azure AI Speech** (SSML con prosodia calmada:
  `pausada` = rate -8 %, pitch -2 %, y una pausa extra entre oraciones).
  Máximo 1500 caracteres (`413`). Se configura con `AZURE_SPEECH_KEY`,
  `AZURE_SPEECH_REGION` y `TTS_VOZ` (opcional); sin ellas responde `503` con
  `detail` y la app usa la voz del teléfono. Errores de Azure nunca se reenvían
  como `401` (la app cerraría la sesión): `502`/`503`/`504`.

Si tu proveedor de transcripción usa otro contrato (por ejemplo Riva por gRPC),
el único archivo a tocar es `app/voz/stt.py`.

### Preparar Supabase

1. Crea el proyecto en [supabase.com](https://supabase.com) y copia
   `Project Settings -> API`: **Project URL** y clave **service_role**.
2. **SQL Editor -> New query**: pega `supabase/schema.sql` y ejecútalo. Crea las
   tablas `perfiles`, `sesiones`, `mensajes` y `evaluaciones`, sus índices y las
   políticas RLS. Es idempotente: se puede volver a correr.
3. **Authentication -> Providers -> Email**: para el flujo móvil de esta app,
   desactiva *Confirm email*. Si lo dejas activo, `/auth/register` devuelve `202`
   pidiendo que el usuario confirme su correo antes de poder iniciar sesión.
4. Comprueba que todo quedó bien:

   ```bash
   python scripts/verificar_supabase.py
   ```

   Valida las credenciales, que las 4 tablas existan y avisa si *Confirm email*
   sigue activo. No escribe nada en la base de datos.

> Ojo con **cuál** clave copias. La *publishable* (`sb_publishable_...`) es la
> pública del cliente y está sujeta a RLS: con ella el backend no puede escribir
> sesiones ni evaluaciones. La que va en `SUPABASE_SERVICE_ROLE_KEY` es la
> **secret** (`sb_secret_...`) o la *service_role* (`eyJ...`) del formato antiguo.

Modelo de datos:

| Tabla          | Para qué                                                                 |
| -------------- | ------------------------------------------------------------------------ |
| `perfiles`     | Nombre visible del usuario, 1:1 con `auth.users`.                        |
| `sesiones`     | Estado del orquestador (historial + respuestas del test) en `estado` jsonb. |
| `mensajes`     | Transcripción plana de la conversación (auditoría).                      |
| `evaluaciones` | Resultado final del test; es lo que lee la pantalla *Histórico* de la app. |

---

## 3. Correr el servidor

```bash
uvicorn app.main:app --reload
```

Por defecto queda en `http://127.0.0.1:8000`. Documentación interactiva
(Swagger) en `http://127.0.0.1:8000/docs`.

Para que la app móvil llegue desde un dispositivo físico, expón el servidor en
la red local y apunta `EXPO_PUBLIC_API_BASE_URL` a la IP de tu PC:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## 4. Probar la API

### Health check

```bash
curl http://127.0.0.1:8000/salud
# {"status":"ok"}
```

### Crear cuenta e iniciar sesión

```bash
curl -X POST http://127.0.0.1:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"nombre":"María","email":"maria@ejemplo.com","password":"secreta123"}'
```

```json
{
  "token": "eyJ...",
  "user": { "id": "uuid", "nombre": "María", "email": "maria@ejemplo.com" }
}
```

`POST /auth/login` recibe `{email, password}` y devuelve lo mismo. El `token` es
el *access token* de Supabase: mándalo como `Authorization: Bearer <token>` en
el resto de llamadas.

### Enviar un mensaje al asistente

```bash
curl -X POST http://127.0.0.1:8000/chat/mensaje \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
        "mensaje": "hola"
      }'
```

Respuesta esperada:

```json
{
  "sesion_id": "b3f1...-uuid",
  "respuesta": "texto que dice el asistente",
  "protocolo_crisis_activo": false,
  "test_completo": false,
  "ubicacion_solicitada": false,
  "resultado": null,
  "acciones": [],
  "opciones": []
}
```

`acciones` siempre viene (puede ser `[]`): son los botones que la app pinta
debajo del mensaje. Ver la sección 11. `opciones` también (puede ser `[]`):
respuestas rápidas para la última pregunta. Ver la sección 12.

El token es opcional en `/chat/mensaje`: sin él la conversación funciona, pero
la sesión y la evaluación quedan sin dueño y no aparecen en el histórico.

Cuando el asistente cierra el test, `test_completo` pasa a `true` y `resultado`
trae el veredicto ya listo para la app:

```json
{
  "resultado": {
    "score": 16,
    "nivel": "Depresión moderada",
    "interpretacion": "Según la Escala ... tu puntaje total es 16 sobre 52 ...",
    "recomendaciones": ["...", "..."],
    "fecha": "2026-07-25T22:41:09.123456+00:00",
    "sesion_id": "b3f1...-uuid"
  }
}
```

### Histórico de evaluaciones

```bash
curl http://127.0.0.1:8000/evaluaciones \
  -H "Authorization: Bearer <token>"
```

Devuelve la lista de resultados del usuario, más reciente primero.

Guarda el `sesion_id` devuelto y mándalo en los siguientes mensajes para que
el asistente recuerde el hilo de la conversación:

```bash
curl -X POST http://127.0.0.1:8000/chat/mensaje \
  -H "Content-Type: application/json" \
  -d '{
        "sesion_id": "b3f1...-uuid",
        "mensaje": "la verdad esta semana me sentí bastante triste"
      }'
```

Con [HTTPie](https://httpie.io/):

```bash
http POST :8000/chat/mensaje mensaje="hola"
```

Si en algún momento el usuario acepta compartir ubicación (tras que el
asistente llame a `solicitar_ubicacion`, señalado en la respuesta como
`"ubicacion_solicitada": true`), la app cliente debe volver a llamar a
`/chat/mensaje` incluyendo `lat`/`lon`:

```bash
curl -X POST http://127.0.0.1:8000/chat/mensaje \
  -H "Content-Type: application/json" \
  -d '{
        "sesion_id": "b3f1...-uuid",
        "mensaje": "sí, comparto mi ubicación",
        "lat": -13.5183,
        "lon": -71.9781
      }'
```

---

## 5. Tests

```bash
pytest
```

Cubre (sección 12 del documento de diseño):

- `tests/test_modelo.py` — un array de 17 ceros da interpretación "Sin
  depresión"; un array cuya suma supera 22 da "Muy grave"; validación de
  longitud incorrecta de respuestas.
- `tests/test_risk_filter.py` — el filtro determinístico detecta frases de
  riesgo (con y sin tildes/mayúsculas) y no dispara con mensajes neutrales.
- `tests/test_resultado.py` — el adaptador traduce las 3 clases del SVM al
  contrato que consume la app y devuelve todos sus campos.

---

## 6. Estructura del proyecto

```
backend/
├── app/
│   ├── main.py                     # FastAPI app, CORS, lifespan, routers
│   ├── config.py                  # Settings vía pydantic-settings
│   ├── deps.py                    # Usuario actual desde el token de Supabase
│   ├── prompts/system_prompt.py   # System prompt del asistente
│   ├── ml/
│   │   ├── modelo.py               # Carga SVM + scaler, predict()
│   │   └── models/                 # SVM_MODELO.pkl, SVM_scaler.pkl
│   ├── chat/
│   │   ├── tools.py                # Las 5 tools (function calling)
│   │   ├── orchestrator.py         # Loop de tool-use, agnóstico de proveedor
│   │   ├── resultado.py            # Salida del SVM -> contrato de la app
│   │   └── session_store.py        # Sesiones: memoria (dev) o Supabase
│   ├── llm/
│   │   ├── base.py                 # Contrato e historial en formato neutro
│   │   ├── openai_compat.py        # DeepSeek, NVIDIA, Groq, OpenAI, Ollama...
│   │   ├── anthropic_provider.py   # Claude (SDK oficial, cliente async)
│   │   └── factory.py              # Presets y selección por .env
│   ├── voz/
│   │   └── stt.py                  # Transcripción (contrato tipo Whisper)
│   ├── db/
│   │   ├── supabase.py             # Cliente async de GoTrue + PostgREST
│   │   └── repositorio.py          # Perfiles, mensajes y evaluaciones
│   ├── safety/
│   │   ├── risk_filter.py          # Filtro determinístico de palabras clave
│   │   └── recursos.py             # Línea 113 / SAMU 106 (garantizados)
│   ├── locations/
│   │   ├── centros.py              # Google Places + fallback local
│   │   └── data/csmc_cusco.json    # Directorio semilla (Cusco)
│   └── routers/
│       ├── auth.py                 # POST /auth/register, /auth/login, GET /auth/me
│       ├── chat.py                 # POST /chat/mensaje
│       ├── evaluaciones.py         # GET /evaluaciones
│       ├── voz.py                  # POST /voz/transcribir
│       └── health.py               # GET /salud
├── supabase/
│   └── schema.sql                  # Tablas, índices y políticas RLS
├── scripts/
│   └── actualizar_directorio_csmc.py  # Ver sección 7 de este README
├── tests/
├── requirements.txt
└── .env.example
```

---

## 7. Directorio de centros de salud mental (CSMC) — Cusco

`app/locations/data/csmc_cusco.json` viene con una **semilla mínima y de
baja confianza** (nombres reportados públicamente, sin dirección/teléfono/
coordenadas verificados). Antes de producción, complétalo con datos reales
usando:

```bash
python scripts/actualizar_directorio_csmc.py --intentar-descarga
```

**Aviso ya verificado al escribir este proyecto:** la página oficial
(`gob.pe/10942`) bloquea la descarga automática (responde con error 418/403,
anti-bot). El script detecta esto y cae automáticamente al modo manual, que
imprime instrucciones paso a paso para copiar el listado desde el navegador
y pegarlo en un archivo:

```bash
python scripts/actualizar_directorio_csmc.py --manual centros_cusco_pegado.txt
```

El parser reconoce el formato `"<Región>, <Distrito> - Centro de Salud
Mental Comunitaria <Nombre> <Dirección> <Teléfono>"` tal como lo publica
gob.pe, pero **es un parser heurístico sobre texto libre, no un extractor
garantizado** — revisa siempre el resultado impreso en consola antes de
confiar en el JSON generado. Como alternativa más rápida, el propio
documento de diseño sugiere preguntar directamente a la posta/hospital que
ya conozca la universidad.

Mientras `direccion`/`lat`/`lon` sigan en `null` para un centro, el endpoint
de fallback lo devuelve igual como orientación general ("hay un CSMC
reportado en tu provincia, confirma la dirección llamando antes"), **nunca**
en lugar de los recursos garantizados (Línea 113, SAMU 106) — solo además de
ellos.

---

## 8. Despliegue en Render

Variables de entorno del servicio (Dashboard → servicio → Environment). Las
mismas que `.env.example`; las imprescindibles para la app móvil:

| Variable | Obligatoria | Nota |
|---|---|---|
| `LLM_PROVIDER`, `LLM_API_KEY` (+ `LLM_FALLBACK_*`) | Sí | Proveedor de IA |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Sí | Sin ellas `/auth/*` responde 503 |
| `STT_BASE_URL`, `STT_API_KEY`, `STT_MODEL` | Para dictar | Sin ellas `/voz/transcribir` → 503 |
| `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` | Para voz neuronal | Sin ellas `/voz/sintetizar` → 503 (la app usa la voz del teléfono) |
| `TTS_VOZ` | No | Por defecto `es-PE-CamilaNeural` |
| `ALLOWED_ORIGINS`, `ENVIRONMENT` | No | `*` / `production` |

Tras desplegar, `GET /salud` debe mostrar `"supabase": true` y, si configuraste
Azure, `"tts": true`. En el plan gratis de Render el servicio se duerme: la
primera petición tarda ~50 s (la app lo avisa y amplía su timeout).

En Supabase → Authentication → Providers → Email conviene desactivar
"Confirm email" para el flujo móvil (si no, el registro responde `202` sin token
y hay que confirmar el correo antes de entrar).

## 8.1 Notas para producción

- **Sesiones**: `app/chat/session_store.py` define la interfaz
  (`get`/`set`/`append`/`exists`) usada por el orquestador. La implementación
  actual (`MemorySessionStore`) es solo para desarrollo — se pierde al
  reiniciar el proceso y no sirve con múltiples workers. Para producción,
  escribe una `RedisSessionStore` con la misma interfaz y reemplaza la
  instancia `session_store` en ese archivo; no hace falta tocar el
  orquestador ni los routers. El estado de sesión ya se guarda como dicts
  planos (JSON-serializables), pensado para ese cambio.
- **CORS**: `ALLOWED_ORIGINS=*` es cómodo en desarrollo (Expo, etc.) pero
  debe restringirse al dominio real de la app/backend en producción.
- **Logging de crisis**: no se registra el texto completo de mensajes de
  crisis en logs — solo el evento (`protocolo_crisis_activo=True`,
  timestamp, sesión), para no dejar contenido sensible en logs no cifrados.
- **scikit-learn**: fijado a `1.6.1` en `requirements.txt` porque es la
  versión con la que se entrenó/serializó `SVM_MODELO.pkl` y
  `SVM_scaler.pkl`. Si en algún momento se reentrena el modelo con otra
  versión, actualiza el pin — o mejor, no lo cambies sin volver a verificar
  que el pipeline da los mismos resultados.
- **Manejo de errores**: si el modelo `.pkl` no carga (`ModeloNoDisponibleError`),
  el endpoint responde `503` con un mensaje claro. Si la API de Anthropic
  falla, el orquestador devuelve un mensaje conversacional de error en vez de
  dejar el chat "colgado", y el error se loguea con stack trace en el
  servidor (nunca se expone al cliente).

---

## 9. Próximos endpoints previstos (no implementados aún, diseño ya lo permite)

- `POST /chat/iniciar` — mensaje de bienvenida sin esperar input del
  usuario, para que la app móvil abra la conversación con algo mejor que una
  pantalla vacía.
- `POST /reportes/pdf` — reconstruir el PDF clínico que generaba el
  Streamlit original con `reportlab`, a partir del resultado guardado de una
  sesión (`calcular_resultado` ya devuelve todos los datos necesarios:
  predicción ML, puntaje total, interpretación clínica).

Ambos son agregables como nuevos archivos en `app/routers/` sin tocar el
resto del código, gracias a que `main.py` ya está estructurado para incluir
routers adicionales.
---

## 10. Acompañamiento continuo (v2)

El asistente dejó de ser sólo un test: mantiene **un hilo único por persona**,
como una app de mensajería, y recuerda entre conversaciones.

### Hilo continuo

- `POST /chat/mensaje` sin `sesion_id` reutiliza el hilo abierto del usuario en
  lugar de empezar uno nuevo. La app ya no gestiona sesiones.
- `GET /chat/historial` devuelve toda la conversación en orden cronológico.
- Al modelo sólo se le envían los últimos `MAX_MENSAJES_CONTEXTO` (40) mensajes,
  con `recortar_historial`, para que el coste no crezca sin control. La
  continuidad de largo plazo no depende de esa ventana, sino de la memoria.

### Memoria

`app/chat/companero.py` arma un bloque que se añade al system prompt con el
perfil, lo que el asistente ha ido aprendiendo (`preferencias`) y las
actividades pendientes. Por eso recuerda gustos y planes aunque el historial
reciente ya se haya recortado.

### Tools nuevas

| Tool | Qué hace |
| --- | --- |
| `recordar_dato` | Guarda un gusto, afición, meta o dato personal en `preferencias`. |
| `agendar_actividad` | Registra un plan acordado en `actividades`. |
| `listar_actividades` | Consulta lo pendiente para preguntar cómo le fue. |
| `recomendar_musica` | Devuelve enlaces reales de búsqueda en Spotify y YouTube. |

`recomendar_musica` no necesita API ni OAuth: construye URLs de búsqueda, que
siempre llevan a resultados vivos (un id de playlist puede desaparecer).

### Perfil y actividades

- `GET/PUT /perfil` — nombre, edad, ciudad, ocupación y bio.
- `POST /perfil/foto` — sube la imagen al bucket `avatares` de Supabase Storage.
- `GET /actividades`, `PATCH /actividades/{id}` — planes acordados en el chat.

### Migración

Aplicar `supabase/schema_v2.sql` en el SQL Editor (idempotente). Añade las
columnas nuevas de `perfiles`, las tablas `preferencias` y `actividades`, sus
políticas RLS y el bucket de avatares.

---

## 11. Chat como amiga (v4): acciones y ubicación en contexto

### Tono y ritmo

El system prompt (`app/prompts/system_prompt.py`) prioriza la conversación:
escucha reflexiva, validar, preguntas abiertas de una en una, mensajes de 2–4
frases sin listas. El test va **muy espaciado**: nunca en los primeros
intercambios, como máximo un ítem cuando conecte con lo que la persona cuenta,
nunca dos seguidos, y si ya contó algo que responde un ítem se registra sin
preguntarlo; puede completarse a lo largo de varios días. Los 17 ítems, sus
escalas y el protocolo de crisis **no cambian**.

### Ubicación

La app manda `lat`/`lon` en cada mensaje cuando tiene permiso. Ya **no** se pegan
al texto del usuario (que se guarda y se devuelve limpio): van en un bloque
`UBICACIÓN` del system prompt de ese turno ("Ubicación actual disponible: lat,
lon; aprox. en Cusco / fuera de Cusco", radio de 25 km desde la Plaza de Armas).
`buscar_centros_cercanos` usa esas coordenadas si el modelo no pasa otras.

### Acciones

El orquestador acumula, sin duplicados, botones derivados de las tools del
turno (`app/chat/acciones.py`). `POST /chat/mensaje` los devuelve en `acciones`
y se guardan en `mensajes.acciones` (jsonb) con el mensaje de la asistente;
`GET /chat/historial` y `GET /chat/sesiones/{id|ultima}` los devuelven por mensaje
(`null` → `[]`).

| Tool | Acción |
| --- | --- |
| `recomendar_musica` | `{tipo:"musica", titulo, busqueda, spotify_url, youtube_music_url}` |
| `recomendar_recurso` (nueva) | `{tipo:"recurso", titulo, recurso_id}` — ids de la pestaña Recursos de la app |
| `buscar_centros_cercanos` | hasta 3 `{tipo:"centro", titulo, direccion?, telefono?, maps_url, distancia_km?}` + `{tipo:"buscar_mapa", titulo, maps_url}` si hay lat/lon |
| `activar_protocolo_crisis` | `{tipo:"llamar", titulo, telefono:"113"}` y `"106"` |
| `agendar_actividad` | `{tipo:"actividad", titulo, actividad_id}` |

- `maps_url` de un centro: por coordenadas si las tiene; si no, búsqueda por
  nombre + dirección (o distrito) + Cusco. Dirección y teléfono sólo si el
  directorio los trae; nunca se inventan.
- Fuera de Cusco (y sin resultados de Google Places) sólo se ofrece
  `buscar_mapa`, primero, centrado en la persona.

### Migración v4

Ejecutar `supabase/schema_v4.sql` en el SQL Editor (también está al final de
`supabase/instalar_todo.sql`; idempotente). Añade `mensajes.acciones jsonb`.
Mientras no se aplique, el backend reintenta el insert sin la columna, lee el
historial sin ella y deja **un** warning en el log.

## 12. Chat v5: respuestas rápidas, propuesta concreta, modo seguimiento

### Respuestas rápidas (`opciones`)

Tool `ofrecer_opciones(opciones: string[])`. El backend las valida
(`acciones.normalizar_opciones`): textos no vacíos, recortados a 32 caracteres
(por palabra si se puede), sin duplicados (sin distinguir mayúsculas), máximo
5; si quedan menos de 2 → `[]` y el modelo recibe `opciones_invalidas`.
`POST /chat/mensaje` devuelve **siempre** `opciones: string[]`; si el modelo la
llama varias veces en el turno vale la última válida. **No se guardan** en
`mensajes` ni salen en `/chat/historial`: sólo valen para el mensaje actual.

Prompt: en preguntas cerradas, `["Sí","No"]` o variantes ("Sí, me gustaría",
"Ahora no"). En ítems del test, opciones cálidas que mapean 1 a 1 a la escala
(escala_5: Nada/Un poco/Bastante/Mucho/Muchísimo → 0–4; escala_insomnio:
No/A veces/Casi siempre → 0–2; escala_3: No/Un poco/Mucho → 0–2). El ítem 3
(ideación suicida) va sin botones. Nunca en preguntas abiertas.

### Propuesta concreta

Aurora lee señales (encierro, sedentarismo, mal sueño, estrés de estudios,
soledad, rumiación) y propone UNA acción pequeña (caminar 10 min, sol de la
mañana, llamar a alguien, respiración con `recomendar_recurso`, música con
`recomendar_musica`) y ofrece agendarla con `["Sí, agéndalo","Ahora no"]`. Si
acepta, `agendar_actividad` con fecha concreta: el system prompt lleva un
bloque `FECHA Y HORA` (Perú, UTC-5). Máximo una propuesta por respuesta y no en
cada mensaje.

### Modo seguimiento / recuperación (`app/chat/seguimiento.py`)

Se calcula en el backend con `listar_evaluaciones` (las 5 últimas) y se inyecta
como bloque `ESTADO DE LA PERSONA` en el system prompt del turno:

| Situación | Modo |
| --- | --- |
| Sin evaluaciones | normal (ritmo de la PARTE 1) |
| Último nivel "Sin depresión" | **seguimiento** |
| Tendencia "mejora" (baja ≥1 nivel o ≥6 puntos HDRS frente a la anterior) y último nivel ≠ "Depresión severa" | **seguimiento** |
| Resto (estable / empeora / severa) | normal; si empeora, atención extra y sugerir apoyo profesional |

En seguimiento Aurora no inicia ni continúa ítems por su cuenta; conversa de
cómo se siente, logros, prevención de recaídas y planes, y sólo retoma la
evaluación si la persona lo pide o hay señales claras de empeoramiento durante
varios mensajes (con suavidad). Si hay un test a medias, no insiste. El
protocolo de crisis sigue igual. Si la base de datos falla no se añade bloque.

### Crisis: persona de confianza

`activar_protocolo_crisis` añade, tras `llamar` 113 y 106,
`{tipo:"contacto_confianza", titulo:"Avisar a tu persona de confianza"}`. La
app lo resuelve con la persona de confianza guardada en el teléfono. El prompt
anima con tacto a contactarla y **nunca** promete que la app llamará o avisará sola.
