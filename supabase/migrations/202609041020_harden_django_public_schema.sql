-- Harden Django-owned tables that live in Supabase's PostgREST-exposed public schema.
-- Django connects directly through PostgreSQL and does not rely on anon/authenticated
-- PostgREST roles for these tables.

alter default privileges for role postgres in schema public
  revoke all on tables from anon, authenticated;

alter default privileges for role postgres in schema public
  revoke all on sequences from anon, authenticated;

revoke all privileges on table
  public.django_migrations,
  public.django_content_type,
  public.auth_permission,
  public.auth_group,
  public.auth_group_permissions,
  public.auth_user_groups,
  public.auth_user_user_permissions,
  public.django_admin_log,
  public.auth_user,
  public.django_session,
  public.qbet_simulation_availability,
  public.qbet_simulation_run_state
from anon, authenticated;

alter table public.django_migrations enable row level security;
alter table public.django_content_type enable row level security;
alter table public.auth_permission enable row level security;
alter table public.auth_group enable row level security;
alter table public.auth_group_permissions enable row level security;
alter table public.auth_user_groups enable row level security;
alter table public.auth_user_user_permissions enable row level security;
alter table public.django_admin_log enable row level security;
alter table public.auth_user enable row level security;
alter table public.django_session enable row level security;
alter table public.qbet_simulation_availability enable row level security;
alter table public.qbet_simulation_run_state enable row level security;
