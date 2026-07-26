"""
Tests de la capa multi-proveedor (`app/llm/`).

Lo que se verifica es la traducción entre el historial NEUTRO que guarda el
orquestador y el dialecto de cada API. Si esto se rompe, el asistente pierde el
hilo de la conversación o deja de poder llamar a las tools del test.

No hay llamadas de red: sólo conversión de estructuras.
"""

from app.chat.tools import TOOLS
from app.llm.anthropic_provider import _historial_a_anthropic
from app.llm.base import LlamadaTool, RespuestaLLM
from app.llm.openai_compat import (
    OpenAICompatProvider,
    _historial_a_openai,
    _tools_a_openai,
)

HISTORIAL = [
    {"role": "user", "content": "hola"},
    {
        "role": "assistant",
        "content": "Entiendo.",
        "tool_calls": [
            {"id": "c1", "nombre": "registrar_respuesta", "argumentos": {"item": 1, "valor": 2}},
            {"id": "c2", "nombre": "solicitar_ubicacion", "argumentos": {}},
        ],
    },
    {"role": "tool", "tool_call_id": "c1", "name": "registrar_respuesta", "content": '{"ok":true}'},
    {"role": "tool", "tool_call_id": "c2", "name": "solicitar_ubicacion", "content": "{}"},
]


def test_tools_se_traducen_al_esquema_de_openai():
    convertidas = _tools_a_openai(TOOLS)

    assert len(convertidas) == len(TOOLS)
    primera = convertidas[0]
    assert primera["type"] == "function"
    assert primera["function"]["name"] == "registrar_respuesta"
    # OpenAI llama `parameters` a lo que Anthropic llama `input_schema`.
    assert primera["function"]["parameters"] == TOOLS[0]["input_schema"]


def test_historial_neutro_a_openai():
    mensajes = _historial_a_openai("system prompt", HISTORIAL)

    assert mensajes[0] == {"role": "system", "content": "system prompt"}
    assert mensajes[1] == {"role": "user", "content": "hola"}

    asistente = mensajes[2]
    assert asistente["role"] == "assistant"
    assert len(asistente["tool_calls"]) == 2
    # Los argumentos viajan como string JSON, no como objeto.
    assert asistente["tool_calls"][0]["function"]["arguments"] == '{"item": 1, "valor": 2}'

    assert mensajes[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"ok":true}'}
    assert mensajes[4]["tool_call_id"] == "c2"


def test_historial_neutro_a_anthropic_agrupa_los_tool_results():
    mensajes = _historial_a_anthropic(HISTORIAL)

    # user / assistant(text+tool_use) / user(tool_result x2)
    assert len(mensajes) == 3

    bloques_asistente = mensajes[1]["content"]
    assert bloques_asistente[0] == {"type": "text", "text": "Entiendo."}
    assert bloques_asistente[1]["type"] == "tool_use"
    assert bloques_asistente[1]["name"] == "registrar_respuesta"

    # Anthropic exige los resultados como bloques dentro de UN mensaje de user.
    resultados = mensajes[2]
    assert resultados["role"] == "user"
    assert [b["tool_use_id"] for b in resultados["content"]] == ["c1", "c2"]


def test_turno_de_asistente_sin_texto_no_queda_vacio_en_anthropic():
    historial = [
        {"role": "user", "content": "hola"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "nombre": "solicitar_ubicacion", "argumentos": {}}],
        },
    ]

    bloques = _historial_a_anthropic(historial)[1]["content"]
    assert bloques and bloques[0]["type"] == "tool_use"


def test_respuesta_llm_se_serializa_al_formato_neutro():
    respuesta = RespuestaLLM(
        texto="hola",
        tool_calls=[LlamadaTool(id="c9", nombre="calcular_resultado", argumentos={"x": 1})],
    )

    assert respuesta.pide_tools
    assert respuesta.a_mensaje() == {
        "role": "assistant",
        "content": "hola",
        "tool_calls": [{"id": "c9", "nombre": "calcular_resultado", "argumentos": {"x": 1}}],
    }


def test_tool_calls_con_argumentos_invalidos_no_revientan():
    proveedor = OpenAICompatProvider(
        nombre="test", base_url="http://x", api_key="k", modelo="m"
    )
    mensaje = {
        "tool_calls": [
            {"id": "a", "function": {"name": "t1", "arguments": "no es json"}},
            {"id": "b", "function": {"name": "t2", "arguments": {"ya": "es objeto"}}},
        ]
    }

    llamadas = proveedor._extraer_tool_calls(mensaje)

    assert llamadas[0].argumentos == {}
    assert llamadas[1].argumentos == {"ya": "es objeto"}
