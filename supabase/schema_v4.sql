-- =============================================================================
-- Migración v4 — Asistente Hamilton ("chat como amiga")
--
-- Añade `mensajes.acciones`: los botones que acompañan a un mensaje de la
-- asistente (música, recursos de la app, centros en Google Maps, llamadas a
-- la Línea 113 / SAMU 106, actividad agendada). Es una lista JSON con el
-- contrato documentado en `app/chat/acciones.py`; `null` en los mensajes del
-- usuario y en los de la asistente sin botones.
--
-- El backend funciona aunque esta migración aún no se haya aplicado (guarda
-- los mensajes sin botones y avisa en el log), pero el historial no mostrará
-- los botones de mensajes antiguos hasta ejecutarla.
--
-- Aplicar en: Supabase Dashboard -> SQL Editor -> New query -> Run.
-- Es idempotente: se puede ejecutar varias veces sin romper nada.
-- Requiere haber aplicado antes `schema.sql`, `schema_v2.sql` y `schema_v3.sql`.
-- =============================================================================

alter table public.mensajes add column if not exists acciones jsonb;

-- PostgREST cachea el esquema: se le avisa para que vea la columna nueva sin
-- esperar ni reiniciar el proyecto.
notify pgrst, 'reload schema';
