-- =============================================================================
-- Migración v3 — Asistente Hamilton
--
-- Añade el seguimiento de actividades no cumplidas (contrato v3):
--   * `completada_en`        — cuándo se marcó hecha (para la ficha clínica).
--   * `motivo_no_realizada`  — qué le impidió hacerla, contado por la persona.
--
-- El estado `vencida` NO se guarda: lo calcula el backend comparando
-- `para_cuando` con la hora actual (ver `app/db/repositorio.py`), así nunca
-- queda desincronizado con el paso del tiempo.
--
-- Aplicar en: Supabase Dashboard -> SQL Editor -> New query -> Run.
-- Es idempotente: se puede ejecutar varias veces sin romper nada.
-- Requiere haber aplicado antes `schema.sql` y `schema_v2.sql`.
-- =============================================================================

-- --------------------------------------------------------------------------
-- Seguimiento de actividades
-- --------------------------------------------------------------------------
alter table public.actividades add column if not exists completada_en       timestamptz;
alter table public.actividades add column if not exists motivo_no_realizada text;

-- --------------------------------------------------------------------------
-- Índice para el listado por estado.
--
-- La pantalla de Actividades y la memoria del asistente piden siempre "lo del
-- usuario X ordenado por fecha prevista", que es exactamente este índice.
-- El de v2 (usuario_id, completada, para_cuando) sigue sirviendo para el
-- filtro por `completada`; éste evita el sort cuando se piden todas.
-- --------------------------------------------------------------------------
create index if not exists actividades_usuario_fecha_idx
  on public.actividades (usuario_id, para_cuando);
