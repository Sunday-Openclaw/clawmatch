-- Upgrade an existing conversations table to the current Clawborate v1 conversation schema.
-- Use this only for databases that were created from an older CONVERSATIONS_SCHEMA.sql.

alter table public.conversations
  add column if not exists summary_for_owner text,
  add column if not exists recommended_next_step text,
  add column if not exists last_agent_decision text,
  add column if not exists updated_at timestamptz not null default now();

-- Broaden allowed states if the original table was created with a narrower check.
alter table public.conversations drop constraint if exists conversations_status_check;
alter table public.conversations
  add constraint conversations_status_check
  check (
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
  );

create index if not exists conversations_status_updated_idx
on public.conversations (status, updated_at desc);
