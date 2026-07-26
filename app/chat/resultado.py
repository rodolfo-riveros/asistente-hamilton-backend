"""
Adaptador entre la salida cruda del modelo ML y el contrato que consume la app.

El pipeline de inferencia (`app/ml/modelo.py`) NO se toca: sustenta la tesis.
Aquí sólo se traduce su salida al shape que la app móvil ya espera
(`HamiltonResult` en `src/types/chat.ts`):

    {score, nivel, interpretacion, recomendaciones[], fecha, sesion_id}

El `nivel` sale de la clase predicha por el SVM (fuente de verdad clínica del
trabajo), no del puntaje. La banda por puntaje total se incluye dentro del
texto de `interpretacion`, que es informativo.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Clases del SVM -> niveles del contrato de la app.
# El SVM tiene 3 clases; "Depresión leve" no es una salida posible del modelo.
NIVEL_POR_CLASE_ML: dict[str, str] = {
    "Sin depresión": "Sin depresión",
    "Depresión moderada": "Depresión moderada",
    "Depresión grave": "Depresión severa",
}

NIVEL_POR_DEFECTO = "Depresión moderada"

RECOMENDACIONES: dict[str, list[str]] = {
    "Sin depresión": [
        "Mantén tus rutinas de sueño, alimentación y actividad física.",
        "Conserva el contacto con las personas que te hacen bien.",
        "Si algo cambia y te sientes distinto, vuelve a conversar conmigo.",
    ],
    "Depresión leve": [
        "Comparte lo que sientes con alguien de confianza esta semana.",
        "Intenta sostener horarios regulares de sueño y algo de movimiento diario.",
        "Si el malestar persiste más de dos semanas, consulta con un profesional.",
    ],
    "Depresión moderada": [
        "Conversa con un profesional de salud mental en tu centro de salud más cercano.",
        "Mantén rutinas regulares de sueño y actividad física moderada.",
        "Considera agendar una cita en un Centro de Salud Mental Comunitario cercano.",
    ],
    "Depresión severa": [
        "Busca atención profesional lo antes posible; no tienes que resolverlo solo/a.",
        "Llama a la Línea 113 (opción 5) del MINSA: orientación psicológica gratuita 24/7.",
        "Pide a alguien de confianza que te acompañe a un Centro de Salud Mental Comunitario.",
    ],
}


def construir_resultado(resultado_ml: dict, sesion_id: str) -> dict:
    """
    Convierte la salida de `calcular_resultado` al contrato de la app.

    `resultado_ml` viene con: prediccion_ml, resultado_ml, puntaje_total,
    interpretacion_clinica.
    """
    clase = str(resultado_ml.get("resultado_ml", ""))
    nivel = NIVEL_POR_CLASE_ML.get(clase, NIVEL_POR_DEFECTO)
    score = int(resultado_ml.get("puntaje_total", 0))
    banda = str(resultado_ml.get("interpretacion_clinica", "")).strip()

    interpretacion = (
        f"Según la Escala de Evaluación de Depresión de Hamilton (HDRS-17), tu puntaje "
        f"total es {score} sobre 52, lo que corresponde a una intensidad de síntomas "
        f"«{banda.lower()}». El modelo de clasificación entrenado ubica tu perfil en "
        f"«{clase.lower()}». Este resultado es orientativo y no reemplaza un "
        f"diagnóstico clínico."
    )

    return {
        "score": score,
        "nivel": nivel,
        "interpretacion": interpretacion,
        "recomendaciones": RECOMENDACIONES.get(nivel, RECOMENDACIONES[NIVEL_POR_DEFECTO]),
        "fecha": datetime.now(timezone.utc).isoformat(),
        "sesion_id": sesion_id,
    }
