"""
Tests de la voz en la nube (`POST /voz/sintetizar`, Azure AI Speech).

Sin red: la llamada a Azure se sustituye por un `httpx.MockTransport` y la
autenticación por un `dependency_overrides`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.deps import Usuario, usuario_actual
from app.main import app
from app.voz import tts

MP3_FALSO = b"ID3\x03\x00\x00\x00fake-mp3"


@pytest.fixture
def azure_configurado(monkeypatch):
    monkeypatch.setattr(settings, "azure_speech_key", "clave-de-prueba")
    monkeypatch.setattr(settings, "azure_speech_region", "eastus")
    monkeypatch.setattr(settings, "tts_voz", "es-PE-CamilaNeural")


@pytest.fixture
def azure_mock(monkeypatch):
    """Instala un cliente httpx falso y devuelve la lista de peticiones recibidas."""
    peticiones: list[httpx.Request] = []
    estado = {"respuesta": httpx.Response(200, content=MP3_FALSO)}

    def handler(request: httpx.Request) -> httpx.Response:
        peticiones.append(request)
        resp = estado["respuesta"]
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(tts, "_cliente", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return peticiones, estado


@pytest.fixture
def cliente_autenticado():
    app.dependency_overrides[usuario_actual] = lambda: Usuario(
        id="u-1", email="ana@example.com", nombre="Ana"
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- SSML


def test_ssml_es_xml_valido_y_escapa_el_texto():
    ssml = tts.construir_ssml('Hola <b>"Ana"</b> & tú\x00', "es-PE-CamilaNeural", "pausada")
    raiz = ET.fromstring(ssml)  # lanzaría si el escape fallara

    ns = "{http://www.w3.org/2001/10/synthesis}"
    voz = raiz.find(f"{ns}voice")
    prosodia = voz.find(f"{ns}prosody")
    assert raiz.get("{http://www.w3.org/XML/1998/namespace}lang") == "es-PE"
    assert voz.get("name") == "es-PE-CamilaNeural"
    assert prosodia.get("rate") == "-8%"
    assert prosodia.get("pitch") == "-2%"
    assert prosodia.text == 'Hola <b>"Ana"</b> & tú'


def test_ritmo_normal_no_ralentiza():
    raiz = ET.fromstring(tts.construir_ssml("Hola", "es-MX-DaliaNeural", "normal"))
    prosodia = raiz.find(".//{http://www.w3.org/2001/10/synthesis}prosody")
    assert prosodia.get("rate") == "0%"


@pytest.mark.parametrize(
    "voz,valida",
    [
        ("es-PE-CamilaNeural", True),
        ("es-MX-DaliaNeural", True),
        ("en-US-JennyNeural", False),
        ('es-PE-Camila"/><x', False),
        ("", False),
    ],
)
def test_validacion_de_voz(voz, valida):
    assert tts.voz_valida(voz) is valida


# --------------------------------------------------------------------- Servicio


async def test_sintetizar_llama_a_azure_con_las_cabeceras_correctas(azure_configurado, azure_mock):
    peticiones, _ = azure_mock

    audio = await tts.sintetizar("Hola, ¿cómo estás?")

    assert audio == MP3_FALSO
    (req,) = peticiones
    assert str(req.url) == "https://eastus.tts.speech.microsoft.com/cognitiveservices/v1"
    assert req.headers["Ocp-Apim-Subscription-Key"] == "clave-de-prueba"
    assert req.headers["X-Microsoft-OutputFormat"] == "audio-24khz-48kbitrate-mono-mp3"
    assert req.headers["Content-Type"] == "application/ssml+xml"
    assert "es-PE-CamilaNeural" in req.content.decode("utf-8")


async def test_sin_configuracion_es_503(monkeypatch):
    monkeypatch.setattr(settings, "azure_speech_key", "")
    with pytest.raises(tts.TTSError) as exc:
        await tts.sintetizar("Hola")
    assert exc.value.status_code == 503


async def test_texto_demasiado_largo_es_413(azure_configurado, azure_mock):
    with pytest.raises(tts.TTSError) as exc:
        await tts.sintetizar("a" * (tts.MAX_CARACTERES + 1))
    assert exc.value.status_code == 413
    assert azure_mock[0] == []  # ni siquiera se llamó a Azure


@pytest.mark.parametrize("status_azure,status_esperado", [(401, 502), (403, 502), (429, 503), (500, 502)])
async def test_errores_de_azure_no_se_reenvian_como_401(
    azure_configurado, azure_mock, status_azure, status_esperado
):
    _, estado = azure_mock
    estado["respuesta"] = httpx.Response(status_azure, text="nope")

    with pytest.raises(tts.TTSError) as exc:
        await tts.sintetizar("Hola")
    assert exc.value.status_code == status_esperado


async def test_timeout_de_azure_es_504(azure_configurado, azure_mock):
    _, estado = azure_mock
    estado["respuesta"] = httpx.ReadTimeout("lento")

    with pytest.raises(tts.TTSError) as exc:
        await tts.sintetizar("Hola")
    assert exc.value.status_code == 504


# ----------------------------------------------------------------------- Router


def test_endpoint_devuelve_mp3(azure_configurado, azure_mock, cliente_autenticado):
    resp = cliente_autenticado.post("/voz/sintetizar", json={"texto": "Hola", "ritmo": "normal"})

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/mpeg"
    assert resp.content == MP3_FALSO


def test_endpoint_sin_configurar_responde_detail(monkeypatch, cliente_autenticado):
    monkeypatch.setattr(settings, "azure_speech_key", "")
    resp = cliente_autenticado.post("/voz/sintetizar", json={"texto": "Hola"})

    assert resp.status_code == 503
    assert "AZURE_SPEECH_KEY" in resp.json()["detail"]


def test_endpoint_rechaza_voz_desconocida(azure_configurado, azure_mock, cliente_autenticado):
    resp = cliente_autenticado.post("/voz/sintetizar", json={"texto": "Hola", "voz": "en-US-X"})
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)


def test_endpoint_rechaza_ritmo_invalido(cliente_autenticado):
    resp = cliente_autenticado.post("/voz/sintetizar", json={"texto": "Hola", "ritmo": "rapida"})
    assert resp.status_code == 422


def test_endpoint_exige_sesion():
    resp = TestClient(app).post("/voz/sintetizar", json={"texto": "Hola"})
    assert resp.status_code == 401
