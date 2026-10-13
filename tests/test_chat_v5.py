"""
Tests del chat v5: respuestas rápidas, modo seguimiento y persona de confianza.

Sin red: proveedor LLM con guion, store en memoria y repositorio parcheado.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.chat import acciones as acc
from app.chat import orchestrator, seguimiento
from app.chat.session_store import MemorySessionStore
from app.config import settings
from app.db import repositorio
from app.llm.base import RespuestaLLM
from app.main import app
from app.prompts.system_prompt import SYSTEM_PROMPT
from app.routers import chat as chat_router
from tests.test_acciones import ProveedorGuion, _salida, _tools, _usar

AHORA = datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(monkeypatch):
    """Store en memoria y sin memoria de Supabase (nada sale a la red)."""
    monkeypatch.setattr(orchestrator, "session_store", MemorySessionStore())

    async def sin_memoria(_usuario_id):
        return ""

    monkeypatch.setattr(orchestrator, "memoria_de", sin_memoria)


def _evaluaciones(monkeypatch, filas: list[dict] | Exception):
    async def listar(usuario_id, limite=50):
        if isinstance(filas, Exception):
            raise filas
        return filas

    monkeypatch.setattr(repositorio, "listar_evaluaciones", listar)


def _ev(nivel: str, score: int, fecha: str = "2026-10-10T12:00:00+00:00") -> dict:
    return {"nivel": nivel, "score": score, "fecha": fecha}


# --------------------------------------------------------------------------- #
# Respuestas rápidas: validación
# --------------------------------------------------------------------------- #


def test_normalizar_opciones_limpia_recorta_y_deduplica():
    largas = "Sí, me encantaría intentarlo mañana temprano"
    opciones = acc.normalizar_opciones(["  Sí ", "sí", "No", "", 3, None, largas, "  Ahora   no  "])
    assert opciones[:2] == ["Sí", "No"]
    assert all(len(o) <= acc.MAX_LARGO_OPCION for o in opciones)
    # Se corta por palabra, sin dejar una coma colgando.
    assert opciones[2] == "Sí, me encantaría intentarlo"
    assert opciones[3] == "Ahora no"


def test_normalizar_opciones_maximo_cinco_y_minimo_dos():
    seis = ["Nada", "Un poco", "Bastante", "Mucho", "Muchísimo", "Extra"]
    assert acc.normalizar_opciones(seis) == seis[:5]
    assert acc.normalizar_opciones(["Sí"]) == []
    assert acc.normalizar_opciones(["Sí", "SÍ"]) == []
    assert acc.normalizar_opciones("Sí, No") == []
    assert acc.normalizar_opciones(None) == []


def test_palabra_sin_espacios_se_corta_en_32():
    assert acc.normalizar_opciones(["a" * 40, "No"]) == ["a" * 32, "No"]


def test_tool_ofrecer_opciones_declarada():
    from app.chat.tools import TOOLS

    tool = next(t for t in TOOLS if t["name"] == "ofrecer_opciones")
    esquema = tool["input_schema"]["properties"]["opciones"]
    assert esquema["type"] == "array" and esquema["maxItems"] == 5
    assert tool["input_schema"]["required"] == ["opciones"]


def test_prompt_habla_de_opciones_y_seguimiento():
    assert "`ofrecer_opciones`" in SYSTEM_PROMPT
    assert "MODO SEGUIMIENTO" in SYSTEM_PROMPT
    assert '"Sí, agéndalo", "Ahora no"' in SYSTEM_PROMPT
    # Las escalas clínicas siguen intactas.
    assert "escala_5 → {Ausente:0, Ligero:1, Moderado:2, Intenso:3, Extremo:4}" in SYSTEM_PROMPT
    assert "escala_insomnio → {Ausente:0, Ocasional:1, Frecuente:2}" in SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# Respuestas rápidas: orquestador y contrato HTTP
# --------------------------------------------------------------------------- #


async def test_orquestador_devuelve_la_ultima_llamada_valida(monkeypatch, store):
    _usar(
        monkeypatch,
        ProveedorGuion(
            [
                _tools(("ofrecer_opciones", {"opciones": ["Sí", "No"]})),
                _tools(("ofrecer_opciones", {"opciones": ["Sí, agéndalo", "Ahora no"]})),
                # Inválida (una sola opción): no pisa la anterior y el modelo recibe un error.
                _tools(("ofrecer_opciones", {"opciones": ["Vale"]})),
                RespuestaLLM(texto="¿Lo agendamos para mañana?"),
            ]
        ),
    )
    salida = await orchestrator.procesar_mensaje("v5-1", "no salgo de mi cuarto")

    assert salida["opciones"] == ["Sí, agéndalo", "Ahora no"]
    assert salida["acciones"] == []
    estado = await orchestrator.session_store.get("v5-1")
    errores = [m for m in estado["historial"] if m.get("role") == "tool" and "opciones_invalidas" in m["content"]]
    assert len(errores) == 1


async def test_sin_tool_las_opciones_son_lista_vacia(monkeypatch, store):
    _usar(monkeypatch, ProveedorGuion([RespuestaLLM(texto="Cuéntame más.")]))
    salida = await orchestrator.procesar_mensaje("v5-2", "hola")
    assert salida["opciones"] == []


def test_post_mensaje_devuelve_opciones_y_no_las_guarda(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "https://x.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_role_key", "service")
    guardado: dict = {}

    async def procesar(**_k):
        return {**_salida([]), "opciones": ["Sí", "No", "sí"]}

    async def guardar(sesion_id, usuario_id, mensajes):
        guardado["mensajes"] = mensajes

    monkeypatch.setattr(chat_router, "procesar_mensaje", procesar)
    monkeypatch.setattr(repositorio, "guardar_mensajes", guardar)

    resp = TestClient(app).post("/chat/mensaje", json={"mensaje": "hola"})

    assert resp.status_code == 200
    assert resp.json()["opciones"] == ["Sí", "No"]
    # Sólo texto (y acciones) en la transcripción: las opciones no se persisten.
    assert guardado["mensajes"] == [("user", "hola"), ("assistant", "Aquí tienes.", None)]
    assert "Sí" not in str(guardado["mensajes"][1][2])


def test_post_mensaje_sin_opciones_devuelve_lista_vacia(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "")

    async def procesar(**_k):
        return _salida([])  # sin clave `opciones`

    monkeypatch.setattr(chat_router, "procesar_mensaje", procesar)
    resp = TestClient(app).post("/chat/mensaje", json={"mensaje": "hola"})
    assert resp.json()["opciones"] == []


# --------------------------------------------------------------------------- #
# Modo seguimiento
# --------------------------------------------------------------------------- #


def test_estado_sin_evaluaciones():
    estado = seguimiento.calcular_estado([], AHORA)
    assert estado["modo"] == "sin_evaluaciones" and estado["tendencia"] is None


def test_sin_depresion_es_seguimiento_y_cuenta_dias():
    estado = seguimiento.calcular_estado([_ev("Sin depresión", 5)], AHORA)
    assert estado["modo"] == "seguimiento"
    assert estado["dias_desde_ultima"] == 3
    assert estado["tendencia"] is None


@pytest.mark.parametrize(
    ("ultima", "anterior", "tendencia", "modo"),
    [
        (_ev("Depresión moderada", 15), _ev("Depresión severa", 25), "mejora", "seguimiento"),
        # Mismo nivel pero baja ≥ 6 puntos: mejora clara.
        (_ev("Depresión moderada", 12), _ev("Depresión moderada", 18), "mejora", "seguimiento"),
        # Baja < 6 puntos sin cambiar de nivel: estable.
        (_ev("Depresión moderada", 14), _ev("Depresión moderada", 18), "estable", "evaluacion"),
        (_ev("Depresión severa", 26), _ev("Depresión moderada", 16), "empeora", "evaluacion"),
        # Mejora de puntaje pero sigue severa: no se deja el test.
        (_ev("Depresión severa", 30), _ev("Depresión severa", 40), "mejora", "evaluacion"),
    ],
)
def test_tendencia_y_modo(ultima, anterior, tendencia, modo):
    estado = seguimiento.calcular_estado([ultima, anterior], AHORA)
    assert estado["tendencia"] == tendencia
    assert estado["modo"] == modo


def test_bloque_seguimiento_con_test_a_medias():
    bloque = seguimiento.bloque_estado(seguimiento.calcular_estado([_ev("Sin depresión", 4)], AHORA), 6)
    assert "MODO SEGUIMIENTO" in bloque
    assert "NO inicies ni continúes ítems del test" in bloque
    assert "6 de 17" in bloque


def test_bloque_evaluacion_que_empeora():
    estado = seguimiento.calcular_estado(
        [_ev("Depresión severa", 26), _ev("Depresión moderada", 16)], AHORA
    )
    bloque = seguimiento.bloque_estado(estado)
    assert "MODO SEGUIMIENTO" not in bloque
    assert "ritmo normal del test" in bloque and "peor que la anterior" in bloque


async def test_prompt_del_turno_lleva_modo_seguimiento(monkeypatch, store):
    _evaluaciones(monkeypatch, [_ev("Sin depresión", 3), _ev("Depresión moderada", 15)])
    proveedor = _usar(monkeypatch, ProveedorGuion([RespuestaLLM(texto="¡Qué bueno verte!")]))

    await orchestrator.procesar_mensaje("v5-3", "hola!", usuario_id="u-1")

    system = proveedor.llamadas[0]["system"]
    assert "ESTADO DE LA PERSONA (calculado" in system and "MODO SEGUIMIENTO" in system
    assert "Tendencia respecto a la anterior: mejora" in system
    assert "FECHA Y HORA" in system


async def test_prompt_del_turno_modo_normal(monkeypatch, store):
    _evaluaciones(monkeypatch, [_ev("Depresión moderada", 16)])
    proveedor = _usar(monkeypatch, ProveedorGuion([RespuestaLLM(texto="Hola")]))

    await orchestrator.procesar_mensaje("v5-4", "hola", usuario_id="u-1")

    system = proveedor.llamadas[0]["system"]
    assert "Última evaluación: Depresión moderada (16/52)" in system
    assert "MODO SEGUIMIENTO: " not in system


async def test_sin_usuario_o_con_fallo_no_hay_bloque(monkeypatch, store):
    _evaluaciones(monkeypatch, RuntimeError("supabase caído"))
    proveedor = _usar(
        monkeypatch, ProveedorGuion([RespuestaLLM(texto="a"), RespuestaLLM(texto="b")])
    )

    await orchestrator.procesar_mensaje("v5-5", "hola", usuario_id="u-1")
    await orchestrator.procesar_mensaje("v5-6", "hola")

    for llamada in proveedor.llamadas:
        assert "ESTADO DE LA PERSONA (calculado" not in llamada["system"]


def test_contexto_fecha_en_hora_de_peru():
    texto = orchestrator.contexto_fecha(AHORA)
    assert "martes 2026-10-13 10:00" in texto


# --------------------------------------------------------------------------- #
# Crisis: persona de confianza
# --------------------------------------------------------------------------- #


async def test_crisis_anade_contacto_confianza(monkeypatch, store):
    _usar(
        monkeypatch,
        ProveedorGuion(
            [
                _tools(("activar_protocolo_crisis", {"motivo": "x", "nivel_urgencia": "alto"})),
                RespuestaLLM(texto="Estoy contigo."),
            ]
        ),
    )
    salida = await orchestrator.procesar_mensaje("v5-7", "no quiero vivir")

    tipos = [a["tipo"] for a in salida["acciones"]]
    assert tipos == ["llamar", "llamar", "contacto_confianza"]
    assert salida["acciones"][2] == {
        "tipo": "contacto_confianza",
        "titulo": "Avisar a tu persona de confianza",
    }
    estado = await orchestrator.session_store.get("v5-7")
    tool_msg = next(m for m in estado["historial"] if m.get("role") == "tool")
    assert "NO llama ni avisa sola" in tool_msg["content"]


def test_contacto_confianza_cumple_el_contrato_http():
    validas = chat_router.acciones_validas(acc.acciones_crisis())
    assert [a.tipo for a in validas] == ["llamar", "llamar", "contacto_confianza"]


def test_prompt_crisis_no_promete_llamar_sola():
    assert "NUNCA digas ni insinúes que la app o tú" in SYSTEM_PROMPT
