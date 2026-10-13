"""
Tests del chat "como amiga" (v4): acciones, ubicación en contexto y migración.

Sin red: el proveedor LLM es un guion falso, Google Places y Supabase se
parchean. Cubre:
- Cada tool genera su `Accion` según el contrato de la app.
- `POST /chat/mensaje` devuelve siempre `acciones` y las persiste con el mensaje.
- La ubicación va en el system prompt, no en el texto del usuario.
- Sin la columna `mensajes.acciones` (schema_v4 sin aplicar) todo sigue funcionando.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.chat import acciones as acc
from app.chat import orchestrator
from app.chat.session_store import MemorySessionStore
from app.config import settings
from app.db import repositorio, supabase
from app.llm.base import LlamadaTool, RespuestaLLM
from app.locations import centros
from app.main import app
from app.routers import chat as chat_router

# Plaza de Armas de Cusco y Lima (fuera de Cusco).
CUSCO = (-13.5170, -71.9785)
LIMA = (-12.0464, -77.0428)

DIRECTORIO = [
    {
        "nombre": "CSMC Amanecer",
        "distrito": "Santiago",
        "direccion": "Av. La Cultura Mz. B Lote 5, Santiago",
        "telefono": "084234567",
        "lat": None,
        "lon": None,
    },
    {"nombre": "CSMC Sin Datos", "distrito": "Wanchaq", "direccion": None, "telefono": None},
]


# --------------------------------------------------------------------------- #
# Infraestructura: proveedor LLM con guion y aislamiento de red
# --------------------------------------------------------------------------- #


class ProveedorGuion:
    """Devuelve las respuestas preparadas en orden y recuerda lo que recibió."""

    nombre = "guion"
    modelo = "falso"

    def __init__(self, respuestas: list[RespuestaLLM]) -> None:
        self.respuestas = list(respuestas)
        self.llamadas: list[dict] = []

    async def generar(self, *, system, mensajes, tools, max_tokens):
        self.llamadas.append({"system": system, "mensajes": [dict(m) for m in mensajes]})
        return self.respuestas.pop(0)


def _tools(*llamadas: tuple[str, dict]) -> RespuestaLLM:
    return RespuestaLLM(
        texto="",
        tool_calls=[LlamadaTool(id=f"c{i}", nombre=n, argumentos=a) for i, (n, a) in enumerate(llamadas)],
    )


@pytest.fixture
def sin_red(monkeypatch):
    """Store en memoria, sin Google Places y directorio local fijo."""
    monkeypatch.setattr(orchestrator, "session_store", MemorySessionStore())

    async def sin_google(*_a, **_k):
        return []

    monkeypatch.setattr(centros, "_buscar_google_places", sin_google)
    monkeypatch.setattr(centros, "cargar_directorio_local_csmc", lambda *a, **k: DIRECTORIO)

    # Memoria y evaluaciones vacías: nada de Supabase real aunque haya usuario.
    async def vacio(*_a, **_k):
        return []

    async def sin_memoria(*_a, **_k):
        return ""

    monkeypatch.setattr(orchestrator, "memoria_de", sin_memoria)
    monkeypatch.setattr(repositorio, "listar_evaluaciones", vacio)


def _usar(monkeypatch, proveedor: ProveedorGuion) -> ProveedorGuion:
    monkeypatch.setattr(orchestrator, "get_proveedor", lambda: proveedor)
    return proveedor


# --------------------------------------------------------------------------- #
# Constructores de acciones
# --------------------------------------------------------------------------- #


def test_accion_musica_con_enlaces_universales():
    accion = acc.accion_musica("lofi para estudiar")
    assert accion == {
        "tipo": "musica",
        "titulo": "Lofi para estudiar",
        "busqueda": "lofi para estudiar",
        "spotify_url": "https://open.spotify.com/search/lofi%20para%20estudiar",
        "youtube_music_url": "https://music.youtube.com/search?q=lofi+para+estudiar",
    }
    assert acc.accion_musica("  ") is None


def test_recursos_coinciden_con_los_ids_de_la_app():
    assert set(acc.RECURSOS_APP) == {
        "guia-depresion",
        "respiracion-478",
        "escaneo-corporal",
        "tres-cosas-buenas",
        "grounding-54321",
        "higiene-sueno",
        "activacion-conductual",
    }
    assert acc.accion_recurso("respiracion-478") == {
        "tipo": "recurso",
        "titulo": "Respiración 4-7-8",
        "recurso_id": "respiracion-478",
    }
    assert acc.accion_recurso("inventado") is None


def test_tool_recomendar_recurso_restringe_el_enum():
    from app.chat.tools import TOOLS

    tool = next(t for t in TOOLS if t["name"] == "recomendar_recurso")
    assert tool["input_schema"]["properties"]["recurso_id"]["enum"] == list(acc.RECURSOS_APP)
    centros_tool = next(t for t in TOOLS if t["name"] == "buscar_centros_cercanos")
    assert "required" not in centros_tool["input_schema"]


def test_centros_en_cusco_sin_inventar_datos():
    resultado = {"centros_cercanos": DIRECTORIO, "desde_google": False}
    lista = acc.acciones_centros(resultado, *CUSCO, fuera_de_cusco=False)

    centros_acc = [a for a in lista if a["tipo"] == "centro"]
    assert [c["titulo"] for c in centros_acc] == ["CSMC Amanecer", "CSMC Sin Datos"]
    amanecer, sin_datos = centros_acc
    assert amanecer["telefono"] == "084234567"
    assert amanecer["maps_url"].startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "CSMC+Amanecer" in amanecer["maps_url"]
    # Sin teléfono ni dirección en el directorio: no se inventan.
    assert sin_datos["telefono"] is None and sin_datos["direccion"] is None
    assert sin_datos["distancia_km"] is None
    # Y el botón para buscar alrededor de la persona, al final.
    assert lista[-1]["tipo"] == "buscar_mapa"
    assert "@-13.517,-71.9785,14z" in lista[-1]["maps_url"]


def test_centros_con_coordenadas_se_ordenan_por_distancia_y_maximo_tres():
    lejos = [
        {"nombre": f"C{i}", "lat": CUSCO[0] + i * 0.01, "lon": CUSCO[1]} for i in range(5, 0, -1)
    ]
    lista = acc.acciones_centros(
        {"centros_cercanos": lejos, "desde_google": True}, *CUSCO, fuera_de_cusco=False
    )
    centros_acc = [a for a in lista if a["tipo"] == "centro"]
    assert [c["titulo"] for c in centros_acc] == ["C1", "C2", "C3"]
    assert centros_acc[0]["distancia_km"] == pytest.approx(1.1, abs=0.1)
    assert "query=-13.507,-71.9785" in centros_acc[0]["maps_url"]


def test_fuera_de_cusco_solo_buscar_en_mapa():
    assert not acc.en_cusco(*LIMA)
    lista = acc.acciones_centros(
        {"centros_cercanos": DIRECTORIO, "desde_google": False}, *LIMA, fuera_de_cusco=True
    )
    assert [a["tipo"] for a in lista] == ["buscar_mapa"]


def test_sin_ubicacion_centros_generales_sin_buscar_mapa():
    lista = acc.acciones_centros(
        {"centros_cercanos": DIRECTORIO, "desde_google": False}, None, None, fuera_de_cusco=False
    )
    assert [a["tipo"] for a in lista] == ["centro", "centro"]


def test_agregar_no_duplica():
    destino: list[dict] = []
    acc.agregar(destino, acc.acciones_crisis())
    acc.agregar(destino, [*acc.acciones_crisis(), acc.accion_musica("Lofi"), acc.accion_musica("lofi")])
    assert [a["tipo"] for a in destino] == ["llamar", "llamar", "contacto_confianza", "musica"]


# --------------------------------------------------------------------------- #
# Orquestador: acciones por tool y ubicación fuera del texto
# --------------------------------------------------------------------------- #


async def test_buscar_centros_usa_la_ubicacion_del_turno(monkeypatch, sin_red):
    proveedor = _usar(
        monkeypatch,
        ProveedorGuion([_tools(("buscar_centros_cercanos", {})), RespuestaLLM(texto="Te dejo dos.")]),
    )

    salida = await orchestrator.procesar_mensaje("s-1", "donde me atiendo?", lat=CUSCO[0], lon=CUSCO[1])

    # Ubicación en el system prompt, no en el mensaje.
    system = proveedor.llamadas[0]["system"]
    assert "Ubicación actual disponible: -13.51700, -71.97850; aprox. en Cusco" in system
    assert proveedor.llamadas[0]["mensajes"][-1] == {"role": "user", "content": "donde me atiendo?"}

    estado = await orchestrator._cargar_estado("s-1")
    assert all("Ubicación" not in str(m.get("content")) for m in estado.historial if m["role"] == "user")

    tipos = [a["tipo"] for a in salida["acciones"]]
    assert tipos == ["centro", "centro", "buscar_mapa"]
    assert salida["respuesta"] == "Te dejo dos."


async def test_sin_ubicacion_el_prompt_lo_dice(monkeypatch, sin_red):
    proveedor = _usar(monkeypatch, ProveedorGuion([RespuestaLLM(texto="hola")]))
    salida = await orchestrator.procesar_mensaje("s-2", "hola")

    assert "No hay ubicación en este turno" in proveedor.llamadas[0]["system"]
    assert salida["acciones"] == []


async def test_fuera_de_cusco_el_prompt_y_la_tool_lo_saben(monkeypatch, sin_red):
    proveedor = _usar(
        monkeypatch,
        ProveedorGuion([_tools(("buscar_centros_cercanos", {})), RespuestaLLM(texto="ok")]),
    )
    salida = await orchestrator.procesar_mensaje("s-3", "estoy en lima", lat=LIMA[0], lon=LIMA[1])

    assert "fuera de Cusco" in proveedor.llamadas[0]["system"]
    resultado_tool = proveedor.llamadas[1]["mensajes"][-1]
    assert resultado_tool["role"] == "tool" and "fuera de Cusco" in resultado_tool["content"]
    assert [a["tipo"] for a in salida["acciones"]] == ["buscar_mapa"]


async def test_crisis_musica_recurso_sin_duplicados(monkeypatch, sin_red):
    _usar(
        monkeypatch,
        ProveedorGuion(
            [
                _tools(
                    ("activar_protocolo_crisis", {"motivo": "x", "nivel_urgencia": "medio"}),
                    ("recomendar_musica", {"busqueda": "música tranquila"}),
                    ("recomendar_recurso", {"recurso_id": "grounding-54321"}),
                ),
                _tools(
                    ("activar_protocolo_crisis", {"motivo": "x", "nivel_urgencia": "medio"}),
                    ("recomendar_musica", {"busqueda": "música tranquila"}),
                ),
                RespuestaLLM(texto="Estoy aquí contigo."),
            ]
        ),
    )
    salida = await orchestrator.procesar_mensaje("s-4", "no puedo mas")

    assert salida["protocolo_crisis_activo"] is True
    assert salida["acciones"] == [
        {"tipo": "llamar", "titulo": "Línea 113, opción 5", "telefono": "113"},
        {"tipo": "llamar", "titulo": "SAMU 106 (emergencias)", "telefono": "106"},
        {"tipo": "contacto_confianza", "titulo": "Avisar a tu persona de confianza"},
        acc.accion_musica("música tranquila"),
        {"tipo": "recurso", "titulo": "Grounding 5-4-3-2-1", "recurso_id": "grounding-54321"},
    ]


async def test_recurso_desconocido_no_genera_boton(monkeypatch, sin_red):
    _usar(
        monkeypatch,
        ProveedorGuion([_tools(("recomendar_recurso", {"recurso_id": "yoga"})), RespuestaLLM(texto="ok")]),
    )
    salida = await orchestrator.procesar_mensaje("s-5", "algo para relajarme")
    assert salida["acciones"] == []


async def test_agendar_actividad_devuelve_su_id(monkeypatch, sin_red):
    async def crear_actividad(**kwargs):
        return {"id": "act-9", "titulo": kwargs["titulo"]}

    monkeypatch.setattr(repositorio, "crear_actividad", crear_actividad)
    _usar(
        monkeypatch,
        ProveedorGuion(
            [_tools(("agendar_actividad", {"titulo": "Caminar 15 min"})), RespuestaLLM(texto="¡Hecho!")]
        ),
    )
    salida = await orchestrator.procesar_mensaje("s-6", "mañana camino", usuario_id="u-1")

    assert salida["acciones"] == [
        {"tipo": "actividad", "titulo": "Caminar 15 min", "actividad_id": "act-9"}
    ]


# --------------------------------------------------------------------------- #
# Contrato HTTP
# --------------------------------------------------------------------------- #


@pytest.fixture
def cliente_supabase(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "https://x.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_role_key", "service")
    return TestClient(app)


def _salida(acciones):
    return {
        "respuesta": "Aquí tienes.",
        "protocolo_crisis_activo": False,
        "test_completo": False,
        "ubicacion_solicitada": False,
        "acciones": acciones,
        "resultado": None,
        "resultado_ml": None,
        "respuestas": {},
    }


def test_post_mensaje_devuelve_y_persiste_acciones(monkeypatch, cliente_supabase):
    acciones = [
        acc.accion_musica("lofi"),
        {
            "tipo": "centro",
            "titulo": "CSMC Amanecer",
            "direccion": None,
            "telefono": None,
            "maps_url": "https://www.google.com/maps/search/?api=1&query=x",
            "distancia_km": None,
        },
    ]
    recibido: dict = {}

    async def procesar(**kwargs):
        recibido["procesar"] = kwargs
        return _salida(acciones)

    async def guardar(sesion_id, usuario_id, mensajes):
        recibido["guardado"] = mensajes

    monkeypatch.setattr(chat_router, "procesar_mensaje", procesar)
    monkeypatch.setattr(repositorio, "guardar_mensajes", guardar)

    resp = cliente_supabase.post("/chat/mensaje", json={"mensaje": "hola", "lat": -13.5, "lon": -71.9})

    assert resp.status_code == 200
    assert resp.json()["acciones"] == acciones
    assert recibido["procesar"]["lat"] == -13.5
    # El texto del usuario se guarda limpio; las acciones van con la asistente.
    assert recibido["guardado"] == [("user", "hola"), ("assistant", "Aquí tienes.", acciones)]


def test_post_mensaje_sin_acciones_devuelve_lista_vacia(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "")

    async def procesar(**_k):
        return _salida([])

    monkeypatch.setattr(chat_router, "procesar_mensaje", procesar)
    resp = TestClient(app).post("/chat/mensaje", json={"mensaje": "hola"})

    assert resp.status_code == 200
    assert resp.json()["acciones"] == []


def test_historial_devuelve_acciones(monkeypatch, cliente_supabase):
    from app.deps import Usuario, usuario_actual

    async def historial(usuario_id, limite=200):
        return [
            {"rol": "user", "contenido": "hola", "creado_en": "t1", "acciones": []},
            {
                "rol": "assistant",
                "contenido": "toma",
                "creado_en": "t2",
                "acciones": [acc.accion_recurso("higiene-sueno"), {"tipo": "raro"}],
            },
        ]

    monkeypatch.setattr(repositorio, "historial_usuario", historial)
    app.dependency_overrides[usuario_actual] = lambda: Usuario(id="u-1", email=None, nombre="Ana")
    try:
        resp = cliente_supabase.get("/chat/historial")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo[0]["acciones"] == []
    # La acción malformada se descarta sin romper el historial.
    assert cuerpo[1]["acciones"] == [acc.accion_recurso("higiene-sueno")]


# --------------------------------------------------------------------------- #
# Repositorio: tolerancia a la columna `acciones` ausente
# --------------------------------------------------------------------------- #


@pytest.fixture
def aviso_reiniciado(monkeypatch):
    monkeypatch.setattr(repositorio, "_aviso_sin_columna_acciones", False)


async def test_guardar_sin_acciones_no_envia_la_columna(monkeypatch, aviso_reiniciado):
    insertados = []

    async def insert(tabla, filas, upsert=False):
        insertados.append(filas)
        return filas

    monkeypatch.setattr(supabase, "insert", insert)
    await repositorio.guardar_mensajes("s", "u", [("user", "hola"), ("assistant", "hey", [])])

    assert len(insertados) == 1
    assert all("acciones" not in f for f in insertados[0])


async def test_guardar_con_acciones_las_envia_en_todas_las_filas(monkeypatch, aviso_reiniciado):
    insertados = []

    async def insert(tabla, filas, upsert=False):
        insertados.append(filas)
        return filas

    monkeypatch.setattr(supabase, "insert", insert)
    musica = [acc.accion_musica("lofi")]
    await repositorio.guardar_mensajes("s", "u", [("user", "hola"), ("assistant", "hey", musica)])

    assert [f["acciones"] for f in insertados[0]] == [None, musica]


async def test_sin_columna_reintenta_y_avisa_una_vez(monkeypatch, aviso_reiniciado, caplog):
    insertados = []

    async def insert(tabla, filas, upsert=False):
        if any("acciones" in f for f in filas):
            raise supabase.SupabaseError(
                "Could not find the 'acciones' column of 'mensajes' in the schema cache",
                status_code=400,
            )
        insertados.append(filas)
        return filas

    monkeypatch.setattr(supabase, "insert", insert)
    musica = [acc.accion_musica("lofi")]
    with caplog.at_level(logging.WARNING, logger="app.db.repositorio"):
        for _ in range(2):
            await repositorio.guardar_mensajes("s", "u", [("user", "a"), ("assistant", "b", musica)])

    assert len(insertados) == 2
    assert insertados[0] == [
        {"sesion_id": "s", "usuario_id": "u", "rol": "user", "contenido": "a"},
        {"sesion_id": "s", "usuario_id": "u", "rol": "assistant", "contenido": "b"},
    ]
    avisos = [r for r in caplog.records if "schema_v4" in r.getMessage()]
    assert len(avisos) == 1


async def test_historial_sin_columna_repite_la_consulta(monkeypatch, aviso_reiniciado):
    consultas = []

    async def select(tabla, params=None):
        consultas.append(params["select"])
        if "acciones" in params["select"]:
            raise supabase.SupabaseError("column mensajes.acciones does not exist", status_code=400)
        return [{"rol": "user", "contenido": "hola", "creado_en": "t"}]

    monkeypatch.setattr(supabase, "select", select)
    filas = await repositorio.historial_usuario("u")

    assert consultas == ["rol,contenido,creado_en,acciones", "rol,contenido,creado_en"]
    assert filas == [{"rol": "user", "contenido": "hola", "creado_en": "t", "acciones": []}]


async def test_historial_convierte_null_en_lista(monkeypatch):
    async def select(tabla, params=None):
        return [{"rol": "assistant", "contenido": "x", "creado_en": "t", "acciones": None}]

    monkeypatch.setattr(supabase, "select", select)
    filas = await repositorio.obtener_mensajes("s", "u")
    assert filas[0]["acciones"] == []


async def test_otros_errores_de_lectura_se_propagan(monkeypatch):
    async def select(tabla, params=None):
        raise supabase.SupabaseError("boom", status_code=503)

    monkeypatch.setattr(supabase, "select", select)
    with pytest.raises(supabase.SupabaseError):
        await repositorio.historial_usuario("u")


def test_migracion_v4_en_instalar_todo():
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1] / "supabase"
    v4 = (raiz / "schema_v4.sql").read_text(encoding="utf-8")
    todo = (raiz / "instalar_todo.sql").read_text(encoding="utf-8")
    sentencia = "alter table public.mensajes add column if not exists acciones jsonb;"
    assert sentencia in v4
    assert todo.rstrip().endswith(v4.rstrip())
