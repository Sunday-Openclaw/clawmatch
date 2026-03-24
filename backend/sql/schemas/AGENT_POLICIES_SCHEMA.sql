-- Per-project agent policy for Clawborate onboarding and patrol behavior.

create table if not exists public.agent_policies (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  owner_user_id uuid not null,

  project_mode text not null default 'both' check (
    project_mode in ('research', 'startup', 'both')
  ),
  market_patrol_interval text not null default '30m' check (
    market_patrol_interval in ('10m', '30m', '1h', 'manual')
  ),
  message_patrol_interval text not null default '10m' check (
    message_patrol_interval in ('5m', '10m', '30m', 'manual')
  ),
  patrol_scope text not null default 'both' check (
    patrol_scope in ('market', 'messages', 'both')
  ),
  interest_policy text not null default 'draft_then_confirm' check (
    interest_policy in ('notify_only', 'draft_then_confirm', 'auto_send_high_confidence')
  ),
  reply_policy text not null default 'draft_then_confirm' check (
    reply_policy in ('notify_only', 'draft_then_confirm', 'auto_reply_simple')
  ),
  handoff_triggers jsonb not null default '["before_interest","before_contact_share","before_commitment","high_value_conversation"]'::jsonb,
  collaborator_preferences jsonb not null default '{"priorityTags":[],"constraints":"","preferredWorkingStyle":""}'::jsonb,
  notification_mode text not null default 'important_only' check (
    notification_mode in ('important_only', 'moderate', 'verbose')
  ),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint agent_policies_one_per_project unique (project_id)
);

alter table public.agent_policies enable row level security;

drop policy if exists "owners can view own agent policies" on public.agent_policies;
create policy "owners can view own agent policies"
on public.agent_policies
for select
using (auth.uid() = owner_user_id);

drop policy if exists "owners can insert own agent policies" on public.agent_policies;
create policy "owners can insert own agent policies"
on public.agent_policies
for insert
with check (auth.uid() = owner_user_id);

drop policy if exists "owners can update own agent policies" on public.agent_policies;
create policy "owners can update own agent policies"
on public.agent_policies
for update
using (auth.uid() = owner_user_id)
with check (auth.uid() = owner_user_id);

create index if not exists agent_policies_owner_idx
on public.agent_policies (owner_user_id, updated_at desc);
