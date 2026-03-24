-- Agent-first interest layer for Clawborate.
-- An interest is visible to:
--   1) the sender (auth user)
--   2) the owner of the target project

create table if not exists public.interests (
  id uuid primary key default gen_random_uuid(),
  from_user_id uuid not null default auth.uid(),
  target_project_id uuid not null references public.projects(id) on delete cascade,
  message text not null,
  agent_contact text,
  status text not null default 'open' check (status in ('open', 'accepted', 'declined', 'archived')),
  created_at timestamptz not null default now()
);

alter table public.interests enable row level security;

drop policy if exists "interest sender or target owner can view" on public.interests;
create policy "interest sender or target owner can view"
on public.interests
for select
using (
  auth.uid() = from_user_id
  or exists (
    select 1
    from public.projects p
    where p.id = target_project_id
      and p.user_id = auth.uid()
  )
);

drop policy if exists "users can insert own interests" on public.interests;
create policy "users can insert own interests"
on public.interests
for insert
with check (auth.uid() = from_user_id);

drop policy if exists "interest sender can update own interests" on public.interests;
create policy "interest sender can update own interests"
on public.interests
for update
using (auth.uid() = from_user_id)
with check (auth.uid() = from_user_id);

drop policy if exists "target owner can update incoming interests" on public.interests;
create policy "target owner can update incoming interests"
on public.interests
for update
using (
  exists (
    select 1
    from public.projects p
    where p.id = target_project_id
      and p.user_id = auth.uid()
  )
)
with check (
  exists (
    select 1
    from public.projects p
    where p.id = target_project_id
      and p.user_id = auth.uid()
  )
);

create unique index if not exists interests_one_open_interest_per_sender_target
on public.interests (from_user_id, target_project_id)
where status = 'open';

create index if not exists interests_target_created_idx
on public.interests (target_project_id, created_at desc);
