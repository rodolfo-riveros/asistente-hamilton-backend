"""
Carga del modelo SVM entrenado y su scaler, y pipeline de inferencia.

IMPORTANTE: esta lógica es la que sustenta la tesis y NO debe alterarse.
Se preserva exactamente el pipeline del Streamlit original:

    X = np.array(respuestas_17).reshape(1, -1)
    X_scaled = scaler.transform(X)
    pred = modelo.predict(X_scaled)[0]
    clases = {0: "Depresión grave", 1: "Depresión moderada", 2: "Sin depresión"}

Con interpretación clínica adicional por puntaje total (suma de los 17 valores).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Sequence

import joblib
import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

CLASES: dict[int, str] = {
    0: "Depresión grave",
    1: "Depresión moderada",
    2: "Sin depresión",
}

N_ITEMS = 17


class ModeloNoDisponibleError(RuntimeError):
    """Se lanza cuando el modelo o el scaler no pudieron cargarse."""


class _ModeloSVM:
    """Envoltura perezosa (lazy) del modelo + scaler, cargados una sola vez."""

    def __init__(self) -> None:
        self._modelo = None
        self._scaler = None

    def _cargar(self) -> None:
        if self._modelo is not None and self._scaler is not None:
            return
        try:
            self._modelo = joblib.load(settings.modelo_path)
            self._scaler = joblib.load(settings.scaler_path)
        except FileNotFoundError as exc:
            logger.exception("No se encontraron los archivos del modelo SVM")
            raise ModeloNoDisponibleError(
                f"No se pudo cargar el modelo ML: {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - queremos capturar cualquier fallo de joblib
            logger.exception("Error inesperado cargando el modelo SVM/scaler")
            raise ModeloNoDisponibleError(
                f"No se pudo cargar el modelo ML: {exc}"
            ) from exc

    def predecir(self, respuestas: Sequence[int]) -> dict:
        """
        Corre el pipeline exacto de inferencia sobre las 17 respuestas.

        Devuelve un dict con la predicción del modelo, el puntaje total y la
        interpretación clínica por rango de puntaje.
        """
        if len(respuestas) != N_ITEMS:
            raise ValueError(
                f"Se esperaban exactamente {N_ITEMS} respuestas, se recibieron {len(respuestas)}"
            )

        self._cargar()
        assert self._modelo is not None and self._scaler is not None  # para mypy/lint

        X = np.array(respuestas).reshape(1, -1)
        X_scaled = self._scaler.transform(X)
        pred = int(self._modelo.predict(X_scaled)[0])

        resultado = CLASES.get(pred, "Resultado no reconocido")
        total = int(sum(respuestas))
        interp = _interpretar_puntaje(total)

        return {
            "prediccion_ml": pred,
            "resultado_ml": resultado,
            "puntaje_total": total,
            "interpretacion_clinica": interp,
        }


def _interpretar_puntaje(total: int) -> str:
    """Interpretación clínica adicional por puntaje total (no altera la predicción ML)."""
    if total <= 7:
        return "Sin depresión"
    elif total <= 13:
        return "Leve"
    elif total <= 18:
        return "Moderada"
    elif total <= 22:
        return "Grave"
    else:
        return "Muy grave"


@lru_cache
def get_modelo() -> _ModeloSVM:
    """Devuelve la instancia única (singleton) del modelo SVM."""
    return _ModeloSVM()


def calcular_resultado(respuestas: Sequence[int]) -> dict:
    """Punto de entrada usado por la tool `calcular_resultado` del orquestador."""
    return get_modelo().predecir(respuestas)
