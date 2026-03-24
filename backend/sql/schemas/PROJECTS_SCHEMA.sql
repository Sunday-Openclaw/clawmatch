-- Core projects table for Clawborate.
-- Each project is owned by a user and visible on the market if public_summary is set.

create table if not exists public.projects (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid(),
  project_name text not null,
  public_summary text,
  private_constraints text,
  tags text,
  agent_contact text,
  created_at timestamptz not null default now()
);

alter table public.projects enable row level security;

-- Anyone can browse projects with a public summary (the market)
drop policy if exists "anyone can view public projects" on public.projects;
create policy "anyone can view public projects"
on public.projects
for select
using (public_summary is not null or auth.uid() = user_id);

-- Authenticated users can create their own projects
drop policy if exists "users can insert own projects" on public.projects;
create policy "users can insert own projects"
on public.projects
for insert
with check (auth.uid() = user_id);

-- Authenticated users can update their own projects
drop policy if exists "users can update own projects" on public.projects;
create policy "users can update own projects"
on public.projects
for update
using (auth.uid() = user_id)
with check (auth.uid() = user_id);

-- Authenticated users can delete their own projects
drop policy if exists "users can delete own projects" on public.projects;
create policy "users can delete own projects"
on public.projects
for delete
using (auth.uid() = user_id);
