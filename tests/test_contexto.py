"""
Tests del recorte de contexto del hilo continuo (`recortar_historial`).

Como la conversación con cada persona ya no se reinicia nunca, el historial
crece sin límite. Recortarlo mal rompe las APIs: si la ventana empieza con un
mensaje `tool` suelto, tanto OpenAI como Anthropic rechazan la petición.
"""

from app.chat.orchestrator import recortar_historial


def _historial(n: int) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(n)]


def test_no_toca_historiales_cortos():
    historial = _historial(10)
    assert recortar_historial(historial, maximo=40) is historial


def test_conserva_los_mensajes_mas_recientes():
    recortado = recortar_historial(_historial(100), maximo=10)

    assert len(recortado) == 10
    assert recortado[-1]["content"] == "m99"


def test_nunca_empieza_con_un_tool_huerfano():
    # El corte natural caería justo sobre los tool_results.
    historial = _historial(20) + [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "nombre": "registrar_respuesta", "argumentos": {}}],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "registrar_respuesta", "content": "{}"},
        {"role": "tool", "tool_call_id": "c2", "name": "otra", "content": "{}"},
        {"role": "assistant", "content": "listo"},
    ]

    recortado = recortar_historial(historial, maximo=3)

    # Retrocede hasta incluir el turno del asistente que pidió esas tools.
    assert recortado[0]["role"] == "assistant"
    assert recortado[0].get("tool_calls")


def test_el_recorte_es_estable_al_repetirse():
    historial = _historial(100)
    una_vez = recortar_historial(historial, maximo=12)
    dos_veces = recortar_historial(una_vez, maximo=12)

    assert una_vez == dos_veces
