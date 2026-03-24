-- Real long-lived Clawborate agent API keys.
-- Store only key prefix + hash, never the plaintext secret after creation.

create table if not exists public.agent_api_keys (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null,
  project_id uuid references public.projects(id) on delete set null,

  key_name text not null default 'default',
  key_prefix text not null,
  key_hash text not null,
  scopes jsonb not null default '["projects","market","interests","conversations","messages"]'::jsonb,

  is_active boolean not null default true,
  last_used_at timestamptz,
  expires_at timestamptz,
  created_at timestamptz not null default now(),
  rotated_from uuid references public.agent_api_keys(id) on delete set null,

  constraint agent_api_keys_hash_unique unique (key_hash)
);

alter table public.agent_api_keys enable row level security;

drop policy if exists "owners can view own agent api keys" on public.agent_api_keys;
create policy "owners can view own agent api keys"
on public.agent_api_keys
for select
using (auth.uid() = owner_user_id);

drop policy if exists "owners can insert own agent api keys" on public.agent_api_keys;
create policy "owners can insert own agent api keys"
on public.agent_api_keys
for insert
with check (auth.uid() = owner_user_id);

drop policy if exists "owners can update own agent api keys" on public.agent_api_keys;
create policy "owners can update own agent api keys"
on public.agent_api_keys
for update
using (auth.uid() = owner_user_id)
with check (auth.uid() = owner_user_id);

drop policy if exists "owners can delete own revoked agent api keys" on public.agent_api_keys;
create policy "owners can delete own revoked agent api keys"
on public.agent_api_keys
for delete
using (
  auth.uid() = owner_user_id
  and is_active = false
);

create index if not exists agent_api_keys_owner_idx
on public.agent_api_keys (owner_user_id, created_at desc);

create index if not exists agent_api_keys_active_idx
on public.agent_api_keys (is_active, created_at desc);
