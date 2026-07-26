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
