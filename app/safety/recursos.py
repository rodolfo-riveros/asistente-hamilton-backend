"""
Recursos de ayuda garantizados (Perú).

Se muestran SIEMPRE al activarse el protocolo de crisis, sin depender de
ubicación ni de ninguna API externa (fuente: Ministerio de Salud del Perú,
gob.pe). Estos recursos nunca deben ser reemplazados por la búsqueda de
centros cercanos — solo complementados por ella.
"""

RECURSOS_NACIONALES_PE = {
    "linea_113": {
        "nombre": "Línea 113, opción 5 (MINSA - Salud Mental)",
        "telefono": "113",
        "detalle": (
            "Orientación psicológica gratuita, confidencial, las 24 horas, "
            "todos los días del año, desde cualquier operador (Movistar, "
            "Claro, Entel, Bitel) o teléfono fijo."
        ),
    },
    "samu": {
        "nombre": "SAMU - Emergencia médica",
        "telefono": "106",
        "detalle": "Para riesgo de vida inmediato.",
    },
}
