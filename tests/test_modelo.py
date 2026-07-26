"""
Tests del pipeline de inferencia ML (sección 12 del documento de diseño):

- Un array de 17 ceros debe dar interpretación "Sin depresión".
- Un array cuya suma sea > 22 debe dar interpretación "Muy grave".
"""

from app.ml.modelo import calcular_resultado


def test_array_de_ceros_da_sin_depresion():
    respuestas = [0] * 17
    resultado = calcular_resultado(respuestas)

    assert resultado["puntaje_total"] == 0
    assert resultado["interpretacion_clinica"] == "Sin depresión"


def test_suma_mayor_a_22_da_muy_grave():
    # Valores máximos por ítem según la escala correspondiente
    # (escala_5 max=4 para la mayoría; escala_insomnio/escala_3 max=2 para 4,12,13,14,16,17)
    respuestas = [4, 4, 4, 2, 4, 4, 4, 4, 4, 4, 4, 2, 2, 2, 4, 2, 2]
    resultado = calcular_resultado(respuestas)

    assert resultado["puntaje_total"] > 22
    assert resultado["interpretacion_clinica"] == "Muy grave"


def test_numero_incorrecto_de_respuestas_lanza_error():
    import pytest

    with pytest.raises(ValueError):
        calcular_resultado([0] * 10)
