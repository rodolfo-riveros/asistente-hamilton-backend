"""Tests del filtro de seguridad determinístico (sección 12)."""

from app.safety.risk_filter import detectar_riesgo


def test_detecta_frase_de_riesgo():
    assert detectar_riesgo("ya no quiero seguir, siento que ya no vale la pena seguir") is True


def test_detecta_variantes_sin_tildes_y_mayusculas():
    assert detectar_riesgo("YA NO AGUANTO MAS, quiero MORIR") is True


def test_no_detecta_frase_neutral():
    assert detectar_riesgo("hoy me sentí bastante tranquilo y con ganas de salir con mis amigos") is False


def test_no_detecta_mensaje_vacio():
    assert detectar_riesgo("") is False
