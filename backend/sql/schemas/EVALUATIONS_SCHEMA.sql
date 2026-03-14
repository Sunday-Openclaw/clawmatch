-- Private evaluations: only visible to the owner who submitted them.
-- Assumes `projects` already exists and auth.uid() is available via Supabase auth.

create table if not exists public.evaluations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid(),
  target_project_id uuid not null references public.projects(id) on delete cascade,
  score integer not null check (score >= 0 and score <= 100),
  confidence double precision not null check (confidence >= 0 and confidence <= 1),
  reason text not null,
  should_connect boolean not null default false,
  created_at timestamptz not null default now()
);

alter table public.evaluations enable row level security;

create policy if not exists "users can view their own evaluations"
on public.evaluations
for select
using (auth.uid() = user_id);

create policy if not exists "users can insert their own evaluations"
on public.evaluations
for insert
with check (auth.uid() = user_id);

create policy if not exists "users can update their own evaluations"
on public.evaluations
for update
using (auth.uid() = user_id)
with check (auth.uid() = user_id);

create index if not exists evaluations_user_target_created_idx
on public.evaluations (user_id, target_project_id, created_at desc);
