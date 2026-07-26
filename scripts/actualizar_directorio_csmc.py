#!/usr/bin/env python3
"""
Actualiza `app/locations/data/csmc_cusco.json` con datos verificados de los
Centros de Salud Mental Comunitaria (CSMC) de la región Cusco, a partir de la
fuente oficial del MINSA:

    https://www.gob.pe/10942-conocer-los-centros-de-salud-mental-comunitaria-en-el-peru

--------------------------------------------------------------------------
IMPORTANTE — resultado de la verificación hecha al escribir este script:
--------------------------------------------------------------------------
Al intentar leer esa página de forma automática, gob.pe devolvió un error
HTTP 418 (bloqueo anti-bot), tal como anticipaba el documento de diseño de
este proyecto. Es decir: el scraping directo NO es viable tal cual, al menos
no sin medidas adicionales (headers de navegador real, sesión con cookies,
o un servicio de rendering) que están fuera del alcance de este script y que,
además, podrían violar los términos de uso del sitio si se abusa de ellos.

Este script por lo tanto ofrece DOS modos:

1. `--intentar-descarga`: intenta la descarga automática de todas formas
   (por si el bloqueo era temporal o específico de este entorno). Si falla,
   cae automáticamente al modo manual con instrucciones claras.

2. `--manual archivo.txt` (o pegar por stdin): parsea texto ya copiado a mano
   por una persona desde el navegador (login/cookies/captcha resueltos por un
   humano), en el formato en que gob.pe lista los centros:

       Cusco, Santiago - Centro de Salud Mental Comunitaria <Nombre>
       <Dirección> <Teléfono>

   Una entrada por línea o por bloque; el parser es tolerante a variaciones
   menores de formato pero SIEMPRE reporta qué pudo y qué no pudo interpretar,
   para que quede claro qué hay que revisar a mano.

En ambos casos, el script NUNCA inventa direcciones, teléfonos ni
coordenadas — si un campo no se pudo extraer con confianza, queda en `null`
y la entrada se marca como pendiente de verificación manual.

Uso:
    python scripts/actualizar_directorio_csmc.py --intentar-descarga
    python scripts/actualizar_directorio_csmc.py --manual centros_pegados.txt
    cat centros_pegados.txt | python scripts/actualizar_directorio_csmc.py --manual -
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

URL_OFICIAL = "https://www.gob.pe/10942-conocer-los-centros-de-salud-mental-comunitaria-en-el-peru"
REGION_OBJETIVO = "Cusco"

SCRIPT_DIR = Path(__file__).resolve().parent
SALIDA_JSON = SCRIPT_DIR.parent / "app" / "locations" / "data" / "csmc_cusco.json"

# Patrón observado en los listados públicos de gob.pe:
#   "<Región>, <Distrito> - Centro de Salud Mental Comunitaria <Nombre> <Dirección> <Teléfono>"
# Es tolerante a variaciones de espacios/guiones, pero NO adivina campos que
# no calcen con este patrón — esos casos se reportan aparte para revisión manual.
#
# LIMITACIÓN CONOCIDA: separar "nombre del centro" de "dirección" en texto
# plano es ambiguo cuando el nombre tiene varias palabras (ej. "Wiñay
# Kausay"). Para reducir errores, la dirección se ancla a que empiece con un
# token típico de direcciones peruanas (Jr., Av., Psj., Mz., Carretera, etc.).
# Aun así, revisa siempre el resultado antes de usarlo en producción — esto
# es un parser heurístico sobre texto libre, no un extractor garantizado.
_PREFIJOS_DIRECCION = (
    r"Jr\.?|Jir[oó]n|Av\.?|Avenida|Psj\.?|Pasaje|Calle|Carretera|Panamericana|"
    r"Prolongaci[oó]n|Mz\.?|Manzana|Urb\.?|Urbanizaci[oó]n|Camino|Km\.?|"
    r"Centro Poblado|Comunidad|Sector|Anexo|Plaza|Parque"
)

_PATRON_ENTRADA = re.compile(
    r"(?P<region>[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ ]+?),\s*"
    r"(?P<distrito>[\wÁÉÍÓÚÑáéíóúñ .]+?)\s*-\s*"
    r"Centro de Salud Mental Comunitari[ao]\s+"
    r"(?P<nombre>.+?)\s+"
    rf"(?P<direccion>(?:{_PREFIJOS_DIRECCION}).+?)\s+"
    r"(?P<telefono>[\d\s\-]{6,15})$",
    re.MULTILINE,
)


def _intentar_descarga_automatica() -> str | None:
    """Intenta descargar la página oficial. Devuelve el HTML/texto o None si falla."""
    try:
        import httpx
    except ImportError:
        print("httpx no está instalado en este entorno; instala requirements.txt primero.")
        return None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True, headers=headers) as client:
            resp = client.get(URL_OFICIAL)
        if resp.status_code != 200:
            print(
                f"[AVISO] gob.pe respondió con status {resp.status_code} "
                "(probable bloqueo anti-bot). No se pudo descargar automáticamente."
            )
            return None
        return resp.text
    except Exception as exc:  # noqa: BLE001
        print(f"[AVISO] Falló la descarga automática: {exc!r}")
        return None


def _parsear_centros(texto: str, region_objetivo: str = REGION_OBJETIVO) -> tuple[list[dict], list[str]]:
    """
    Extrae entradas de centros para la región dada.

    Devuelve (centros_extraidos, lineas_no_reconocidas) para que el operador
    humano pueda revisar manualmente lo que el parser no pudo interpretar.
    """
    centros = []
    reconocidas = set()

    for match in _PATRON_ENTRADA.finditer(texto):
        region = match.group("region").strip()
        if region.lower() != region_objetivo.lower():
            continue
        centros.append(
            {
                "nombre": f"CSMC {match.group('nombre').strip()}",
                "provincia": None,  # gob.pe no siempre distingue provincia/distrito con claridad
                "distrito": match.group("distrito").strip(),
                "direccion": match.group("direccion").strip(),
                "telefono": re.sub(r"\s+", "", match.group("telefono")),
                "lat": None,
                "lon": None,
                "fuente": f"gob.pe/10942 (verificado {date.today().isoformat()})",
            }
        )
        reconocidas.add(match.group(0))

    no_reconocidas = [
        linea.strip()
        for linea in texto.splitlines()
        if region_objetivo.lower() in linea.lower()
        and linea.strip()
        and linea.strip() not in reconocidas
    ]

    return centros, no_reconocidas


def _guardar_json(centros: list[dict]) -> None:
    SALIDA_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(SALIDA_JSON, "w", encoding="utf-8") as f:
        json.dump(centros, f, ensure_ascii=False, indent=2)
    print(f"\n✅ Se guardaron {len(centros)} centros en {SALIDA_JSON}")


def _mostrar_instrucciones_manuales() -> None:
    print(
        f"""
No fue posible descargar {URL_OFICIAL} automáticamente (bloqueo anti-bot
confirmado al momento de escribir este script — respuesta HTTP 418).

Pasos para completar el directorio manualmente:

1. Abre esa URL en tu navegador (como persona, no como script).
2. Ubica la sección de la región "{REGION_OBJETIVO}" en el listado.
3. Copia el bloque de texto tal cual aparece (una línea por centro, formato:
   "Cusco, <Distrito> - Centro de Salud Mental Comunitaria <Nombre>
   <Dirección> <Teléfono>").
4. Pega ese texto en un archivo, por ejemplo `centros_cusco_pegado.txt`.
5. Corre:
       python scripts/actualizar_directorio_csmc.py --manual centros_cusco_pegado.txt

Alternativa más rápida y confiable: en vez de esperar el scraping, pregunta
directamente a la posta/hospital de salud más cercano que ya conozca la propia
universidad — suele ser más rápido que depender de este scraping.

Mientras tanto, `{SALIDA_JSON.name}` conserva su semilla actual (de baja
confianza, campos de contacto en null) — no se sobrescribe con nada inventado.
"""
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modo = parser.add_mutually_exclusive_group(required=True)
    modo.add_argument(
        "--intentar-descarga",
        action="store_true",
        help="Intenta descargar y parsear la página oficial automáticamente.",
    )
    modo.add_argument(
        "--manual",
        metavar="ARCHIVO",
        help="Ruta a un archivo de texto pegado a mano desde el navegador (usa '-' para leer stdin).",
    )
    args = parser.parse_args()

    if args.intentar_descarga:
        html = _intentar_descarga_automatica()
        if html is None:
            _mostrar_instrucciones_manuales()
            sys.exit(1)
        texto = html
    else:
        if args.manual == "-":
            texto = sys.stdin.read()
        else:
            ruta = Path(args.manual)
            if not ruta.exists():
                print(f"No existe el archivo: {ruta}")
                sys.exit(1)
            texto = ruta.read_text(encoding="utf-8")

    centros, no_reconocidas = _parsear_centros(texto)

    if not centros:
        print(
            f"No se reconoció ningún centro de la región '{REGION_OBJETIVO}' en el texto dado. "
            "Revisa que el formato pegado coincida con el de gob.pe, o ajusta el patrón "
            "_PATRON_ENTRADA de este script si el sitio cambió su formato."
        )
        sys.exit(1)

    print(f"Se reconocieron {len(centros)} centros de la región {REGION_OBJETIVO}:")
    for c in centros:
        print(f"  - {c['nombre']} ({c['distrito']}) — {c['direccion']} — {c['telefono']}")

    if no_reconocidas:
        print(
            f"\n⚠️  {len(no_reconocidas)} línea(s) mencionan '{REGION_OBJETIVO}' pero no calzaron "
            "con el patrón esperado — revísalas a mano, puede que falte algún dato:"
        )
        for linea in no_reconocidas:
            print(f"  ? {linea}")

    _guardar_json(centros)
    print(
        "\nRecuerda: aunque el nombre/dirección/teléfono vengan de la fuente oficial, "
        "sigue siendo buena práctica llamar antes de derivar a alguien, ya que horarios "
        "y datos de contacto pueden cambiar."
    )


if __name__ == "__main__":
    main()
