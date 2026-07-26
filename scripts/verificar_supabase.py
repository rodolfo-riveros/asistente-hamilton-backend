"""
Comprueba que la configuración de Supabase del `.env` es usable.

Uso:
    python scripts/verificar_supabase.py

Verifica, en orden:
1. Que SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY estén definidos.
2. Que la clave sea de servidor y no la publicable (error clásico).
3. Que las 4 tablas de `supabase/schema.sql` existan y sean accesibles.
4. Si la confirmación por correo está activa (afecta a /auth/register).

No escribe nada en la base de datos: todas las consultas son de lectura.
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import supabase  # noqa: E402

TABLAS = ["perfiles", "sesiones", "mensajes", "evaluaciones"]

OK = "  [OK]  "
FALLO = " [FALLO] "


async def main() -> int:
    print(f"\nProyecto: {settings.supabase_url or '(sin definir)'}\n")

    if not settings.supabase_habilitado:
        print(f"{FALLO}Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY en el .env")
        return 1

    if settings.supabase_service_role_key.startswith("sb_publishable_"):
        print(f"{FALLO}SUPABASE_SERVICE_ROLE_KEY tiene una clave publicable.")
        print("        Usa la clave 'secret' (sb_secret_...) o la service_role (eyJ...).")
        return 1

    errores = 0

    # --- Tablas ---
    for tabla in TABLAS:
        try:
            await supabase.select(tabla, {"select": "*", "limit": 1})
            print(f"{OK}tabla '{tabla}' accesible")
        except supabase.SupabaseError as exc:
            errores += 1
            print(f"{FALLO}tabla '{tabla}': {exc.mensaje}")
            if "does not exist" in exc.mensaje or "PGRST205" in exc.mensaje:
                print("        Ejecuta supabase/schema.sql en el SQL Editor del dashboard.")

    # --- Auth ---
    try:
        cliente = supabase.get_client()
        respuesta = await cliente.get(
            "/auth/v1/settings", headers={"apikey": settings.supabase_service_role_key}
        )
        ajustes = respuesta.json() if respuesta.is_success else {}
        if respuesta.is_success:
            autoconfirm = ajustes.get("mailer_autoconfirm", False)
            print(f"{OK}Auth responde (email habilitado: {not ajustes.get('disable_signup', False)})")
            if not autoconfirm:
                print(
                    "         Aviso: 'Confirm email' está activo. /auth/register devolverá 202\n"
                    "         pidiendo confirmar el correo. Para el flujo móvil, desactívalo en\n"
                    "         Authentication -> Providers -> Email."
                )
        else:
            errores += 1
            print(f"{FALLO}Auth respondió {respuesta.status_code}")
    except Exception as exc:  # noqa: BLE001 - script de diagnóstico
        errores += 1
        print(f"{FALLO}No se pudo consultar Auth: {exc}")

    await supabase.cerrar_client()

    print()
    if errores:
        print(f"{errores} comprobación(es) fallaron.\n")
        return 1
    print("Todo listo: el backend puede usar Supabase.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
