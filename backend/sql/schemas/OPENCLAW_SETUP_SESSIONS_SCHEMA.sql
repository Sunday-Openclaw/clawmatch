-- One-time setup sessions for Clawborate -> OpenClaw bootstrap.

create table if not exists public.openclaw_setup_sessions (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null,
  token_hash text not null unique,
  status text not null default 'issued' check (
    status in ('issued', 'exchanged', 'applied', 'failed', 'expired')
  ),
  expires_at timestamptz not null,
  consumed_at timestamptz,
  agent_api_key_id uuid references public.agent_api_keys(id) on delete set null,
  install_manifest jsonb not null default '{}'::jsonb,
  client_receipt jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.openclaw_setup_sessions enable row level security;

drop policy if exists "owners can view own openclaw setup sessions" on public.openclaw_setup_sessions;
create policy "owners can view own openclaw setup sessions"
on public.openclaw_setup_sessions
for select
using (auth.uid() = owner_user_id);

create index if not exists openclaw_setup_sessions_owner_idx
on public.openclaw_setup_sessions (owner_user_id, created_at desc);

create index if not exists openclaw_setup_sessions_status_idx
on public.openclaw_setup_sessions (status, expires_at asc);
