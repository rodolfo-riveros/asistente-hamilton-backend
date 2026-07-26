"""
Tests del adaptador entre la salida del modelo ML y el contrato de la app móvil
(`app/chat/resultado.py`). No tocan el pipeline de inferencia.
"""

from app.chat.resultado import RECOMENDACIONES, construir_resultado


def _salida_ml(clase: str, puntaje: int, banda: str) -> dict:
    return {
        "prediccion_ml": 1,
        "resultado_ml": clase,
        "puntaje_total": puntaje,
        "interpretacion_clinica": banda,
    }


def test_mapea_las_tres_clases_del_svm():
    esperado = {
        "Sin depresión": "Sin depresión",
        "Depresión moderada": "Depresión moderada",
        "Depresión grave": "Depresión severa",
    }
    for clase, nivel in esperado.items():
        resultado = construir_resultado(_salida_ml(clase, 10, "Leve"), "ses-1")
        assert resultado["nivel"] == nivel


def test_devuelve_el_contrato_completo_que_espera_la_app():
    resultado = construir_resultado(
        _salida_ml("Depresión moderada", 16, "Moderada"), "ses-abc"
    )

    assert set(resultado) == {
        "score",
        "nivel",
        "interpretacion",
        "recomendaciones",
        "fecha",
        "sesion_id",
    }
    assert resultado["score"] == 16
    assert resultado["sesion_id"] == "ses-abc"
    assert resultado["recomendaciones"] == RECOMENDACIONES["Depresión moderada"]
    # El texto menciona el puntaje y la banda clínica por puntaje total.
    assert "16" in resultado["interpretacion"]
    assert "moderada" in resultado["interpretacion"].lower()


def test_clase_desconocida_no_rompe_y_cae_en_un_nivel_valido():
    resultado = construir_resultado(_salida_ml("otra cosa", 5, "Sin depresión"), "ses-2")
    assert resultado["nivel"] in RECOMENDACIONES
    assert resultado["recomendaciones"]
