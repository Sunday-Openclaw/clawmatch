-- Agent-first conversation layer for ClawMatch.
-- Conversations are private between two users and can optionally originate from an interest.
-- This schema includes the owner-summary / workflow-state fields used by the UI and agent tools.

create table if not exists public.conversations (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  interest_id uuid references public.interests(id) on delete set null,
  initiator_user_id uuid not null,
  receiver_user_id uuid not null,
  status text not null default 'active' check (
    status in (
      'active',
      'mutual',
      'conversation_started',
      'needs_human',
      'handoff_ready',
      'closed_not_fit',
      'paused',
      'closed'
    )
  ),
  summary_for_owner text,
  recommended_next_step text,
  last_agent_decision text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint conversations_distinct_participants check (initiator_user_id <> receiver_user_id)
);

create table if not exists public.conversation_messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  sender_user_id uuid not null,
  sender_agent_name text,
  message text not null,
  created_at timestamptz not null default now()
);

alter table public.conversations enable row level security;
alter table public.conversation_messages enable row level security;

drop policy if exists "conversation participants can view conversations" on public.conversations;
create policy "conversation participants can view conversations"
on public.conversations
for select
using (auth.uid() = initiator_user_id or auth.uid() = receiver_user_id);

drop policy if exists "conversation participants can create conversations" on public.conversations;
create policy "conversation participants can create conversations"
on public.conversations
for insert
with check (auth.uid() = initiator_user_id or auth.uid() = receiver_user_id);

drop policy if exists "conversation participants can update conversations" on public.conversations;
create policy "conversation participants can update conversations"
on public.conversations
for update
using (auth.uid() = initiator_user_id or auth.uid() = receiver_user_id)
with check (auth.uid() = initiator_user_id or auth.uid() = receiver_user_id);

drop policy if exists "conversation participants can view messages" on public.conversation_messages;
create policy "conversation participants can view messages"
on public.conversation_messages
for select
using (
  exists (
    select 1 from public.conversations c
    where c.id = conversation_id
      and (c.initiator_user_id = auth.uid() or c.receiver_user_id = auth.uid())
  )
);

drop policy if exists "conversation participants can send messages" on public.conversation_messages;
create policy "conversation participants can send messages"
on public.conversation_messages
for insert
with check (
  sender_user_id = auth.uid()
  and exists (
    select 1 from public.conversations c
    where c.id = conversation_id
      and (c.initiator_user_id = auth.uid() or c.receiver_user_id = auth.uid())
  )
);

create index if not exists conversations_project_created_idx
on public.conversations (project_id, created_at desc);

create index if not exists conversations_user_a_idx
on public.conversations (initiator_user_id, created_at desc);

create index if not exists conversations_user_b_idx
on public.conversations (receiver_user_id, created_at desc);

create index if not exists conversations_status_updated_idx
on public.conversations (status, updated_at desc);

create index if not exists conversation_messages_conversation_created_idx
on public.conversation_messages (conversation_id, created_at asc);
