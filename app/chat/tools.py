"""
Definición de las tools (function calling) que el asistente puede invocar.

Exactamente estas 5 tools, tal como las especifica el documento de diseño
(sección 6). El orquestador (`orchestrator.py`) es responsable de ejecutar la
función real del backend correspondiente a cada `tool_use` que devuelva Claude.
"""

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
        "description": "Le pide a la app móvil que solicite permiso de ubicación al usuario.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "buscar_centros_cercanos",
        "description": "Busca centros de salud mental cercanos a partir de coordenadas.",
        "input_schema": {
            "type": "object",
            "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}},
            "required": ["lat", "lon"],
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
