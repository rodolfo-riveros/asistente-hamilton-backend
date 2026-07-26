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
