-- =====================================================================
-- Aurora / aliento — instalación completa de la base de datos (v1 + v2 + v3 + v4)
--
-- Para un proyecto de Supabase NUEVO. Pegar entero en:
--   Supabase › SQL Editor › New query › Run
-- Es idempotente: se puede ejecutar más de una vez sin romper nada.
-- Generado a partir de schema.sql, schema_v2.sql, schema_v3.sql y schema_v4.sql.
-- =====================================================================


-- ---------------------------------------------------------------------
-- schema.sql
-- ---------------------------------------------------------------------
-- =============================================================================
-- Esquema de Supabase para "Asistente Hamilton".
--
-- Cómo aplicarlo:
--   Supabase Dashboard -> SQL Editor -> pegar este archivo -> Run.
--   Es idempotente: se puede volver a ejecutar sin romper nada.
--
-- Notas de diseño:
-- - `auth.users` (Supabase Auth) es la fuente de verdad de identidad.
--   `perfiles` sólo guarda datos de aplicación (nombre visible).
-- - El backend escribe con la SERVICE ROLE KEY, que ignora RLS. Las políticas
--   de abajo son la red de seguridad para cualquier acceso con la clave anon.
-- =============================================================================

-- --------------------------------------------------------------------------
-- perfiles: 1:1 con auth.users
-- --------------------------------------------------------------------------
create table if not exists public.perfiles (
  id          uuid primary key references auth.users (id) on delete cascade,
  nombre      text        not null default '',
  email       text,
  creado_en   timestamptz not null default now()
);

-- --------------------------------------------------------------------------
-- sesiones: una conversación completa del test de Hamilton.
-- `estado` guarda el EstadoSesion serializado del orquestador (historial de
-- mensajes con bloques tool_use, respuestas registradas y flags).
-- --------------------------------------------------------------------------
create table if not exists public.sesiones (
  id                      uuid primary key,
  usuario_id              uuid references auth.users (id) on delete cascade,
  estado                  jsonb       not null default '{}'::jsonb,
  protocolo_crisis_activo boolean     not null default false,
  test_completo           boolean     not null default false,
  creado_en               timestamptz not null default now(),
  actualizado_en          timestamptz not null default now()
);

create index if not exists sesiones_usuario_idx
  on public.sesiones (usuario_id, actualizado_en desc);

-- --------------------------------------------------------------------------
-- mensajes: transcripción legible de la conversación (auditoría y continuidad).
-- El historial que consume Claude vive en `sesiones.estado`; esta tabla es la
-- versión plana, sin bloques de tool-use.
-- --------------------------------------------------------------------------
create table if not exists public.mensajes (
  id         bigint generated always as identity primary key,
  sesion_id  uuid        not null references public.sesiones (id) on delete cascade,
  usuario_id uuid references auth.users (id) on delete cascade,
  rol        text        not null check (rol in ('user', 'assistant')),
  contenido  text        not null,
  creado_en  timestamptz not null default now()
);

create index if not exists mensajes_sesion_idx
  on public.mensajes (sesion_id, creado_en);

-- --------------------------------------------------------------------------
-- evaluaciones: resultado final del test (lo que muestra la pantalla Histórico).
-- `prediccion_ml` y `respuestas` se guardan para trazabilidad de la tesis.
-- --------------------------------------------------------------------------
create table if not exists public.evaluaciones (
  id              uuid primary key default gen_random_uuid(),
  sesion_id       uuid references public.sesiones (id) on delete set null,
  usuario_id      uuid references auth.users (id) on delete cascade,
  score           integer     not null,
  nivel           text        not null,
  interpretacion  text        not null,
  recomendaciones jsonb       not null default '[]'::jsonb,
  prediccion_ml   integer,
  respuestas      jsonb,
  creado_en       timestamptz not null default now()
);

create index if not exists evaluaciones_usuario_idx
  on public.evaluaciones (usuario_id, creado_en desc);

-- --------------------------------------------------------------------------
-- actualizado_en automático en sesiones
-- --------------------------------------------------------------------------
create or replace function public.tocar_actualizado_en()
returns trigger
language plpgsql
security invoker
-- search_path vacío a propósito: sin fijarlo, un rol podría anteponer un
-- esquema propio y secuestrar la resolución de nombres dentro de la función.
set search_path = ''
as $$
begin
  new.actualizado_en = now();
  return new;
end;
$$;

drop trigger if exists sesiones_actualizado_en on public.sesiones;
create trigger sesiones_actualizado_en
  before update on public.sesiones
  for each row execute function public.tocar_actualizado_en();

-- =============================================================================
-- Row Level Security
-- =============================================================================
alter table public.perfiles      enable row level security;
alter table public.sesiones      enable row level security;
alter table public.mensajes      enable row level security;
alter table public.evaluaciones  enable row level security;

drop policy if exists "perfil propio" on public.perfiles;
create policy "perfil propio" on public.perfiles
  for all using (auth.uid() = id) with check (auth.uid() = id);

drop policy if exists "sesiones propias" on public.sesiones;
create policy "sesiones propias" on public.sesiones
  for all using (auth.uid() = usuario_id) with check (auth.uid() = usuario_id);

drop policy if exists "mensajes propios" on public.mensajes;
create policy "mensajes propios" on public.mensajes
  for all using (auth.uid() = usuario_id) with check (auth.uid() = usuario_id);

drop policy if exists "evaluaciones propias" on public.evaluaciones;
create policy "evaluaciones propias" on public.evaluaciones
  for all using (auth.uid() = usuario_id) with check (auth.uid() = usuario_id);

-- ---------------------------------------------------------------------
-- schema_v2.sql
-- ---------------------------------------------------------------------
-- =============================================================================
-- Migración v2 — Asistente Hamilton
--
-- Añade lo necesario para que el asistente funcione como acompañante continuo:
--   * perfil ampliado (foto, datos personales, gustos)
--   * memoria a largo plazo (`preferencias`)
--   * actividades acordadas con el usuario (`actividades`)
--
-- Aplicar en: Supabase Dashboard -> SQL Editor -> New query -> Run.
-- Es idempotente: se puede ejecutar varias veces sin romper nada.
-- Requiere haber aplicado antes `schema.sql`.
-- =============================================================================

-- --------------------------------------------------------------------------
-- Perfil ampliado
-- --------------------------------------------------------------------------
alter table public.perfiles add column if not exists foto_url        text;
alter table public.perfiles add column if not exists edad            integer;
alter table public.perfiles add column if not exists ciudad          text;
alter table public.perfiles add column if not exists ocupacion       text;
alter table public.perfiles add column if not exists bio             text;
alter table public.perfiles add column if not exists actualizado_en  timestamptz not null default now();

-- --------------------------------------------------------------------------
-- preferencias: memoria a largo plazo del acompañante.
--
-- Guarda lo que el asistente va aprendiendo de la persona (música que le gusta,
-- cómo se llama su perro, qué le ayuda cuando está mal). Se inyecta en el
-- system prompt para que recuerde entre conversaciones, incluso cuando el
-- historial reciente ya se recortó por límite de contexto.
-- --------------------------------------------------------------------------
create table if not exists public.preferencias (
  id           bigint generated always as identity primary key,
  usuario_id   uuid        not null references auth.users (id) on delete cascade,
  clave        text        not null,
  valor        text        not null,
  creado_en    timestamptz not null default now(),
  actualizado_en timestamptz not null default now(),
  unique (usuario_id, clave)
);

create index if not exists preferencias_usuario_idx
  on public.preferencias (usuario_id, actualizado_en desc);

-- --------------------------------------------------------------------------
-- actividades: lo que el usuario y el asistente acuerdan hacer.
-- --------------------------------------------------------------------------
create table if not exists public.actividades (
  id          uuid primary key default gen_random_uuid(),
  usuario_id  uuid        not null references auth.users (id) on delete cascade,
  sesion_id   uuid references public.sesiones (id) on delete set null,
  titulo      text        not null,
  nota        text,
  categoria   text        not null default 'general',
  para_cuando timestamptz,
  completada  boolean     not null default false,
  creado_en   timestamptz not null default now()
);

create index if not exists actividades_usuario_idx
  on public.actividades (usuario_id, completada, para_cuando);

-- --------------------------------------------------------------------------
-- actualizado_en automático
-- --------------------------------------------------------------------------
drop trigger if exists perfiles_actualizado_en on public.perfiles;
create trigger perfiles_actualizado_en
  before update on public.perfiles
  for each row execute function public.tocar_actualizado_en();

drop trigger if exists preferencias_actualizado_en on public.preferencias;
create trigger preferencias_actualizado_en
  before update on public.preferencias
  for each row execute function public.tocar_actualizado_en();

-- =============================================================================
-- Row Level Security
-- =============================================================================
alter table public.preferencias enable row level security;
alter table public.actividades  enable row level security;

drop policy if exists "preferencias propias" on public.preferencias;
create policy "preferencias propias" on public.preferencias
  for all using (auth.uid() = usuario_id) with check (auth.uid() = usuario_id);

drop policy if exists "actividades propias" on public.actividades;
create policy "actividades propias" on public.actividades
  for all using (auth.uid() = usuario_id) with check (auth.uid() = usuario_id);

-- =============================================================================
-- Storage: fotos de perfil
-- =============================================================================
insert into storage.buckets (id, name, public)
values ('avatares', 'avatares', true)
on conflict (id) do nothing;

-- Sin política de SELECT a propósito: al ser un bucket público, los objetos ya
-- se sirven por su URL directa. Añadir una política amplia sobre
-- storage.objects permitiría además LISTAR el bucket, o sea enumerar las fotos
-- de todos los usuarios. La subida la hace el backend con la service role key.
drop policy if exists "avatares lectura publica" on storage.objects;

-- ---------------------------------------------------------------------
-- schema_v3.sql
-- ---------------------------------------------------------------------
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

-- ---------------------------------------------------------------------
-- schema_v4.sql
-- ---------------------------------------------------------------------
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
