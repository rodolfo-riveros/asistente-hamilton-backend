"""
Tabla de los 17 ítems de la Escala de Hamilton (HDRS-17) tal como los usa este
proyecto.

Es la versión estructurada de lo que el system prompt describe en prosa
(`app/prompts/system_prompt.py`, PARTE 1): mismo número de ítem, misma escala.
Sirve para pintar la ficha de diagnóstico sin duplicar criterios clínicos en
varios sitios.

Las escalas NO se pueden alterar: el modelo SVM de la tesis se entrenó con
ellas.

- escala_5        -> {Ausente:0, Ligero:1, Moderado:2, Intenso:3, Extremo:4}
- escala_3        -> {Ausente:0, Ligero:1, Intenso:2}
- escala_insomnio -> {Ausente:0, Ocasional:1, Frecuente:2}
"""

from __future__ import annotations

from dataclasses import dataclass

MAXIMO_POR_ESCALA: dict[str, int] = {
    "escala_5": 4,
    "escala_3": 2,
    "escala_insomnio": 2,
}


@dataclass(frozen=True)
class Item:
    """Un ítem de la escala: su número, cómo llamarlo y hasta cuánto puntúa."""

    numero: int
    etiqueta: str
    escala: str

    @property
    def maximo(self) -> int:
        return MAXIMO_POR_ESCALA[self.escala]


ITEMS: tuple[Item, ...] = (
    Item(1, "Ánimo deprimido", "escala_5"),
    Item(2, "Sentimientos de culpa", "escala_5"),
    Item(3, "Ideación suicida", "escala_5"),
    Item(4, "Insomnio de conciliación", "escala_insomnio"),
    Item(5, "Despertar a media noche", "escala_5"),
    Item(6, "Despertar muy temprano", "escala_5"),
    Item(7, "Energía e interés", "escala_5"),
    Item(8, "Enlentecimiento y concentración", "escala_5"),
    Item(9, "Agitación", "escala_5"),
    Item(10, "Irritabilidad", "escala_5"),
    Item(11, "Ansiedad somática", "escala_5"),
    Item(12, "Síntomas gastrointestinales", "escala_3"),
    Item(13, "Síntomas somáticos generales", "escala_3"),
    Item(14, "Libido", "escala_3"),
    Item(15, "Hipocondría", "escala_5"),
    Item(16, "Reactividad del ánimo", "escala_3"),
    Item(17, "Pérdida de peso", "escala_3"),
)

TOTAL_ITEMS = len(ITEMS)


def item(numero: int) -> Item:
    """Devuelve el ítem por su número (1..17)."""
    return ITEMS[numero - 1]


def ficha_items(respuestas: dict[int, int] | None) -> list[dict]:
    """
    Los 17 ítems en el contrato de `GET /diagnostico`.

    Siempre devuelve los 17: los que aún no se han preguntado salen con
    `valor: null` y `registrado: false`, para que la app pueda mostrar el avance
    real de la evaluación.
    """
    registradas = respuestas or {}
    ficha: list[dict] = []
    for it in ITEMS:
        valor = registradas.get(it.numero)
        ficha.append(
            {
                "item": it.numero,
                "etiqueta": it.etiqueta,
                "valor": None if valor is None else int(valor),
                "maximo": it.maximo,
                "registrado": valor is not None,
            }
        )
    return ficha


def normalizar_respuestas(crudas: dict | None) -> dict[int, int]:
    """
    Pasa a `{int: int}` las respuestas guardadas en `sesiones.estado`.

    En jsonb las claves son strings, así que llegan como `{"1": 3, ...}`. Se
    descartan las claves que no sean ítems válidos en vez de reventar: el estado
    de sesión es un blob y no queremos que un dato raro tumbe la ficha.
    """
    limpias: dict[int, int] = {}
    for clave, valor in (crudas or {}).items():
        try:
            numero = int(clave)
            entero = int(valor)
        except (TypeError, ValueError):
            continue
        if 1 <= numero <= TOTAL_ITEMS:
            limpias[numero] = entero
    return limpias
