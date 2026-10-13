"""
Definición de las tools (function calling) que el asistente puede invocar.

Las 5 tools clínicas del documento de diseño (sección 6) más las de
acompañamiento cotidiano. El orquestador (`orchestrator.py`) es responsable de ejecutar la
función real del backend correspondiente a cada `tool_use` que devuelva Claude.
"""

from app.chat.acciones import RECURSOS_APP

TOOLS = [
    {
        "name": "registrar_respuesta",
        "description": (
            "Registra la respuesta ya clasificada del usuario para un ítem del "
            "test de Hamilton."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "integer", "description": "Número de ítem, 1 a 17"},
                "valor": {
                    "type": "integer",
                    "description": "Valor en la escala correspondiente al ítem",
                },
            },
            "required": ["item", "valor"],
        },
    },
    {
        "name": "activar_protocolo_crisis",
        "description": (
            "Se llama de inmediato al detectar ideación suicida, autolesión o "
            "riesgo inminente en cualquier punto de la conversación."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "motivo": {"type": "string"},
                "nivel_urgencia": {"type": "string", "enum": ["medio", "alto"]},
            },
            "required": ["motivo", "nivel_urgencia"],
        },
    },
    {
        "name": "solicitar_ubicacion",
        "description": (
            "Le pide a la app móvil que solicite permiso de ubicación al usuario. "
            "No la uses si el contexto ya trae 'Ubicación actual disponible'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "buscar_centros_cercanos",
        "description": (
            "Busca centros de salud mental cercanos. Si no pasas lat/lon se usa la "
            "ubicación actual de la persona (la del contexto) o, si no hay, los centros "
            "generales de Cusco. La app muestra botones para verlos en Google Maps."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}},
        },
    },
    {
        "name": "calcular_resultado",
        "description": (
            "Corre el modelo SVM entrenado (98.70% de exactitud) con las 17 "
            "respuestas para obtener el nivel de depresión."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "respuestas": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 17,
                    "maxItems": 17,
                }
            },
            "required": ["respuestas"],
        },
    },
]

# --- Acompañamiento cotidiano -------------------------------------------------
# Estas tools son las que convierten al asistente en un amigo que recuerda y
# ayuda a hacer cosas, no sólo en un cuestionario clínico.

TOOLS += [
    {
        "name": "recordar_dato",
        "description": (
            "Guarda de forma permanente algo relevante que la persona contó sobre sí "
            "misma para recordarlo en futuras conversaciones: gustos musicales, "
            "aficiones, nombres de personas o mascotas importantes, qué le ayuda "
            "cuando está mal, metas, rutinas, estudios o trabajo. Úsala en cuanto "
            "aparezca el dato, sin anunciarlo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "clave": {
                    "type": "string",
                    "description": (
                        "Etiqueta corta y estable, en minúsculas. Ej: 'musica_favorita', "
                        "'mascota', 'carrera', 'le_calma', 'meta_personal'."
                    ),
                },
                "valor": {
                    "type": "string",
                    "description": "El dato en una frase breve, tal como lo contó.",
                },
            },
            "required": ["clave", "valor"],
        },
    },
    {
        "name": "agendar_actividad",
        "description": (
            "Registra una actividad o plan que la persona se compromete a hacer "
            "(caminar, dormir temprano, escribir, llamar a alguien, ir a una cita). "
            "Sirve para hacerle seguimiento en próximas conversaciones."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "titulo": {"type": "string", "description": "Qué va a hacer, en pocas palabras."},
                "nota": {"type": "string", "description": "Detalle o motivo, opcional."},
                "categoria": {
                    "type": "string",
                    "enum": ["autocuidado", "social", "estudio", "salud", "ocio", "general"],
                },
                "para_cuando": {
                    "type": "string",
                    "description": "Fecha y hora ISO 8601 si la mencionó. Opcional.",
                },
            },
            "required": ["titulo"],
        },
    },
    {
        "name": "listar_actividades",
        "description": (
            "Consulta las actividades pendientes que la persona acordó antes, para "
            "preguntarle cómo le fue o retomarlas."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "registrar_motivo_actividad",
        "description": (
            "Guarda por qué la persona no pudo hacer una actividad que había acordado. "
            "Úsala cuando te cuente el motivo de una actividad vencida (el id te llega "
            "en la memoria de la persona). No la uses para suposiciones tuyas: sólo "
            "cuando ella lo haya explicado."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "actividad_id": {
                    "type": "string",
                    "description": "Id de la actividad vencida, tal como aparece en la memoria.",
                },
                "motivo": {
                    "type": "string",
                    "description": "Lo que impidió hacerla, en una frase y con sus palabras.",
                },
            },
            "required": ["actividad_id", "motivo"],
        },
    },
    {
        "name": "recomendar_musica",
        "description": (
            "Arma recomendaciones de música con enlaces reales de búsqueda en Spotify "
            "y YouTube, según el ánimo y los gustos de la persona. Úsala cuando pida "
            "música o cuando venga bien para acompañar el momento."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "busqueda": {
                    "type": "string",
                    "description": (
                        "Qué buscar, combinando gusto y ánimo. "
                        "Ej: 'rock en español para animarse', 'lofi para estudiar'."
                    ),
                },
                "animo": {
                    "type": "string",
                    "enum": ["calmar", "animar", "acompañar", "concentrar", "dormir"],
                },
            },
            "required": ["busqueda"],
        },
    },
    {
        "name": "recomendar_recurso",
        "description": (
            "Muestra un botón para abrir un recurso de la propia app (pestaña Recursos). "
            "Úsala cuando un ejercicio o lectura concreta le pueda servir ahora: "
            + "; ".join(f"'{rid}' = {titulo}" for rid, titulo in RECURSOS_APP.items())
            + "."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "recurso_id": {"type": "string", "enum": list(RECURSOS_APP)},
                "motivo": {
                    "type": "string",
                    "description": "Por qué le puede servir, en una frase. Opcional.",
                },
            },
            "required": ["recurso_id"],
        },
    },
]

# --- Respuestas rápidas (v5) --------------------------------------------------

TOOLS += [
    {
        "name": "ofrecer_opciones",
        "description": (
            "Muestra botones de respuesta rápida bajo tu mensaje para la pregunta CERRADA "
            "con la que terminas (sí/no, o un ítem del test con opciones cortas que mapean a "
            "su escala). La persona puede pulsar uno o escribir libremente. Nunca en preguntas "
            "abiertas. De 2 a 5 opciones, de 32 caracteres como máximo, sin repetir."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "opciones": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 5,
                    "description": "Ej: ['Sí', 'No'] o ['Nada', 'Un poco', 'Bastante', 'Mucho', 'Muchísimo'].",
                }
            },
            "required": ["opciones"],
        },
    },
]
