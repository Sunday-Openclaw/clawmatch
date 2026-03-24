-- Upgrade Clawborate to the agent-first bootstrap schema and RPC gateway.

alter table public.agent_policies
  add column if not exists interest_behavior text,
  add column if not exists reply_behavior text,
  add column if not exists extra_requirements text not null default '';

do $$
begin
  if exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'agent_policies'
      and column_name = 'extra_requirements'
      and data_type not in ('text', 'character varying')
  ) then
    execute 'alter table public.agent_policies alter column extra_requirements type text using extra_requirements::text';
  end if;
end
$$;

update public.agent_policies
set
  interest_behavior = coalesce(
    nullif(interest_behavior, ''),
    case
      when interest_policy = 'auto_send_high_confidence' then 'direct_send'
      else 'notify_then_send'
    end
  ),
  reply_behavior = coalesce(
    nullif(reply_behavior, ''),
    case
      when reply_policy = 'auto_reply_simple' then 'direct_send'
      else 'notify_then_send'
    end
  ),
  extra_requirements = case
    when nullif(trim(extra_requirements), '') is not null then extra_requirements
    else trim(both E'\n' from concat_ws(E'\n',
      case
        when nullif(collaborator_preferences->>'preferredWorkingStyle', '') is not null
        then 'Preferred working style: ' || (collaborator_preferences->>'preferredWorkingStyle') || '.'
      end,
      case
        when nullif(collaborator_preferences->>'constraints', '') is not null
        then 'Legacy constraints: ' || (collaborator_preferences->>'constraints')
      end,
      case
        when nullif(project_mode, '') is not null
        then 'Legacy project mode preference: ' || project_mode || '.'
      end
    ))
  end;

alter table public.agent_policies
  alter column interest_behavior set default 'notify_then_send',
  alter column interest_behavior set not null,
  alter column reply_behavior set default 'notify_then_send',
  alter column reply_behavior set not null,
  alter column extra_requirements set default '',
  alter column extra_requirements set not null;

alter table public.agent_policies drop constraint if exists agent_policies_interest_behavior_check;
alter table public.agent_policies add constraint agent_policies_interest_behavior_check
check (interest_behavior in ('notify_then_send', 'direct_send'));

alter table public.agent_policies drop constraint if exists agent_policies_reply_behavior_check;
alter table public.agent_policies add constraint agent_policies_reply_behavior_check
check (reply_behavior in ('notify_then_send', 'direct_send'));

alter table public.agent_policies
  drop column if exists project_mode,
  drop column if exists patrol_scope,
  drop column if exists interest_policy,
  drop column if exists reply_policy,
  drop column if exists handoff_triggers,
  drop column if exists collaborator_preferences,
  drop column if exists notification_mode;

alter table public.interests
  add column if not exists source_project_id uuid references public.projects(id) on delete set null;

create index if not exists interests_source_project_created_idx
on public.interests (source_project_id, created_at desc);

alter table public.conversations
  add column if not exists source_project_id uuid references public.projects(id) on delete set null;

update public.conversations c
set source_project_id = i.source_project_id
from public.interests i
where c.source_project_id is null
  and c.interest_id = i.id
  and i.source_project_id is not null;

create index if not exists conversations_source_project_updated_idx
on public.conversations (source_project_id, updated_at desc);

-- Fixed canonical Supabase RPC gateway for long-lived Clawborate agent keys.
-- SECURITY DEFINER variant: keeps explicit typed auth lookup (to avoid RPC 404)
-- while bypassing RLS on agent_api_keys / related tables for agent-key auth.

create extension if not exists pgcrypto;

drop function if exists public.agent_gateway(text, text, jsonb);

create or replace function public.agent_gateway(
    p_agent_key text,
    p_action text,
    p_payload jsonb default '{}'::jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
    v_key_hash text;
    v_key_id uuid;
    v_owner_user_id uuid;
    v_scopes jsonb;
    v_result jsonb;
    v_limit int;
    v_cursor int;
    v_project_id uuid;
    v_source_project_id uuid;
    v_target_project_owner uuid;
    v_conversation_id uuid;
    v_interest_id uuid;
    v_receiver_user_id uuid;
    v_interest_row record;
begin
    v_key_hash := encode(extensions.digest(p_agent_key::text, 'sha256'::text), 'hex');

    select id, owner_user_id, scopes
    into v_key_id, v_owner_user_id, v_scopes
    from public.agent_api_keys
    where key_hash = v_key_hash
      and is_active = true
      and (expires_at is null or expires_at > now())
    limit 1;

    if v_key_id is null then
        return jsonb_build_object('error', 'invalid_agent_key', 'message', 'Invalid or revoked agent key');
    end if;

    update public.agent_api_keys
    set last_used_at = now()
    where id = v_key_id;

    v_limit := greatest(1, least(coalesce(
        case when (p_payload->>'limit') ~ '^\d+$'
             then (p_payload->>'limit')::int
             else null end, 20), 100));
    v_cursor := greatest(0, coalesce(
        case when (p_payload->>'cursor') ~ '^\d+$'
             then (p_payload->>'cursor')::int
             else null end, 0));
    v_project_id := case when nullif(p_payload->>'project_id', '') ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                         then (p_payload->>'project_id')::uuid else null end;
    v_source_project_id := case when nullif(p_payload->>'source_project_id', '') ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                                then (p_payload->>'source_project_id')::uuid else null end;
    v_conversation_id := case when nullif(p_payload->>'conversation_id', '') ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                              then (p_payload->>'conversation_id')::uuid else null end;
    v_interest_id := case when nullif(p_payload->>'interest_id', '') ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                          then (p_payload->>'interest_id')::uuid else null end;
    v_receiver_user_id := case when nullif(p_payload->>'receiver_user_id', '') ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                               then (p_payload->>'receiver_user_id')::uuid else null end;

    case p_action
        when 'get_policy' then
            if not (
                v_scopes ? 'policy'
                or v_scopes ? 'projects'
                or v_scopes ? 'market'
                or v_scopes ? 'conversations'
            ) then
                return jsonb_build_object('error', 'missing_scope', 'message', 'Scope "policy" (or compatible legacy scope) required');
            end if;

            select row_to_json(p.*)::jsonb into v_result
            from public.agent_policies p
            where p.owner_user_id = v_owner_user_id
              and (v_project_id is null or p.project_id = v_project_id)
            order by p.updated_at desc nulls last, p.created_at desc
            limit 1;

        when 'list_market' then
            if not (v_scopes ? 'market') then
                return jsonb_build_object('error', 'missing_scope', 'message', 'Scope "market" required');
            end if;

            select jsonb_agg(t) into v_result
            from (
                select id, user_id, project_name, public_summary, tags, agent_contact, created_at
                from public.projects
                where public_summary is not null
                  and user_id <> v_owner_user_id
                order by created_at desc
                offset v_cursor
                limit v_limit
            ) t;

        when 'list_my_projects' then
            if not (v_scopes ? 'projects') then
                return jsonb_build_object('error', 'missing_scope', 'message', 'Scope "projects" required');
            end if;

            select jsonb_agg(t) into v_result
            from (
                select id, user_id, project_name, public_summary, tags, agent_contact, created_at
                from public.projects
                where user_id = v_owner_user_id
                order by created_at desc
                limit v_limit
            ) t;

        when 'get_project' then
            if not (v_scopes ? 'projects') then
                return jsonb_build_object('error', 'missing_scope', 'message', 'Scope "projects" required');
            end if;

            if v_project_id is null then
                return jsonb_build_object('error', 'missing_project_id', 'message', 'project_id is required');
            end if;

            select row_to_json(p.*)::jsonb into v_result
            from public.projects p
            where p.id = v_project_id
              and p.user_id = v_owner_user_id
            limit 1;

            if v_result is null then
                return jsonb_build_object('error', 'project_not_found', 'message', 'Project not found or not owned by this agent');
            end if;

        when 'create_project', 'create' then
            if not (v_scopes ? 'projects') then
                return jsonb_build_object('error', 'missing_scope', 'message', 'Scope "projects" required');
            end if;

            -- Reject agent keys in user-facing fields
            if coalesce(p_payload->>'agent_contact', '') like '%cm_sk_live_%' then
                return jsonb_build_object('error', 'invalid_field', 'message', 'agent_contact must not contain an API key');
            end if;

            insert into public.projects (
                user_id,
                project_name,
                public_summary,
                private_constraints,
                tags,
                agent_contact
            )
            values (
                v_owner_user_id,
                p_payload->>'project_name',
                p_payload->>'public_summary',
                p_payload->>'private_constraints',
                p_payload->>'tags',
                p_payload->>'agent_contact'
            )
            returning row_to_json(projects.*)::jsonb into v_result;

        when 'update_project', 'update' then
            if not (v_scopes ? 'projects') then
                return jsonb_build_object('error', 'missing_scope', 'message', 'Scope "projects" required');
            end if;

            if v_project_id is null then
                return jsonb_build_object('error', 'missing_project_id', 'message', 'project_id is required');
            end if;

            if not exists (
                select 1
                from public.projects p
                where p.id = v_project_id
                  and p.user_id = v_owner_user_id
            ) then
                return jsonb_build_object('error', 'forbidden_project', 'message', 'Project not found or not owned by this agent');
            end if;

            -- Reject agent keys in user-facing fields
            if coalesce(p_payload->>'agent_contact', '') like '%cm_sk_live_%' then
                return jsonb_build_object('error', 'invalid_field', 'message', 'agent_contact must not contain an API key');
            end if;

            update public.projects
            set project_name = coalesce(p_payload->>'project_name', project_name),
                public_summary = coalesce(p_payload->>'public_summary', public_summary),
                private_constraints = coalesce(p_payload->>'private_constraints', private_constraints),
                tags = coalesce(p_payload->>'tags', tags),
                agent_contact = coalesce(p_payload->>'agent_contact', agent_contact)
            where id = v_project_id
            returning row_to_json(projects.*)::jsonb into v_result;

        when 'delete_project' then
            if not (v_scopes ? 'projects') then
                return jsonb_build_object('error', 'missing_scope', 'message', 'Scope "projects" required');
            end if;

            if v_project_id is null then
                return jsonb_build_object('error', 'missing_project_id', 'message', 'project_id is required');
            end if;

            if not exists (
                select 1
                from public.projects p
                where p.id = v_project_id
                  and p.user_id = v_owner_user_id
            ) then
                return jsonb_build_object('error', 'forbidden_project', 'message', 'Project not found or not owned by this agent');
            end if;

            delete from public.projects
            where id = v_project_id
            returning jsonb_build_object('id', id, 'deleted', true) into v_result;

        when 'list_incoming_interests' then
            if not (v_scopes ? 'interests') then
                return jsonb_build_object('error', 'missing_scope', 'message', 'Scope "interests" required');
            end if;

            select jsonb_agg(t) into v_result
            from (
                select
                    i.id,
                    i.from_user_id,
                    i.source_project_id,
                    i.target_project_id,
                    i.message,
                    i.agent_contact,
                    i.status,
                    i.created_at,
                    jsonb_build_object(
                        'id', p.id,
                        'user_id', p.user_id,
                        'project_name', p.project_name
                    ) as target
                from public.interests i
                join public.projects p on i.target_project_id = p.id
                where p.user_id = v_owner_user_id
                  and (v_project_id is null or i.target_project_id = v_project_id)
                order by i.created_at desc
            ) t;

        when 'list_outgoing_interests' then
            if not (v_scopes ? 'interests') then
                return jsonb_build_object('error', 'missing_scope', 'message', 'Scope "interests" required');
            end if;

            select jsonb_agg(t) into v_result
            from (
                select
                    i.id,
                    i.from_user_id,
                    i.source_project_id,
                    i.target_project_id,
                    i.message,
                    i.agent_contact,
                    i.status,
                    i.created_at,
                    jsonb_build_object(
                        'id', p.id,
                        'user_id', p.user_id,
                        'project_name', p.project_name
                    ) as target
                from public.interests i
                join public.projects p on i.target_project_id = p.id
                where i.from_user_id = v_owner_user_id
                  and (v_source_project_id is null or i.source_project_id = v_source_project_id)
                order by i.created_at desc
            ) t;

        when 'submit_interest' then
            if not (v_scopes ? 'interests') then
                return jsonb_build_object('error', 'missing_scope', 'message', 'Scope "interests" required');
            end if;

            if v_project_id is null then
                return jsonb_build_object('error', 'missing_project_id', 'message', 'project_id is required');
            end if;

            select p.user_id into v_target_project_owner
            from public.projects p
            where p.id = v_project_id
            limit 1;

            if v_target_project_owner is null then
                return jsonb_build_object('error', 'project_not_found', 'message', 'Target project not found');
            end if;

            if v_target_project_owner = v_owner_user_id then
                return jsonb_build_object('error', 'cannot_interest_own_project', 'message', 'Cannot submit interest to your own project');
            end if;

            if v_source_project_id is not null and not exists (
                select 1
                from public.projects p
                where p.id = v_source_project_id
                  and p.user_id = v_owner_user_id
            ) then
                return jsonb_build_object('error', 'forbidden_source_project', 'message', 'source_project_id is not owned by this agent');
            end if;

            if exists (
                select 1
                from public.interests i
                where i.from_user_id = v_owner_user_id
                  and i.target_project_id = v_project_id
                  and i.status in ('open', 'accepted')
                  and (v_source_project_id is null or i.source_project_id = v_source_project_id)
            ) then
                return jsonb_build_object('error', 'duplicate_interest', 'message', 'An open or accepted interest already exists for this project');
            end if;

            -- Reject agent keys in user-facing fields
            if coalesce(p_payload->>'message', '') like '%cm_sk_live_%'
               or coalesce(p_payload->>'agent_contact', p_payload->>'contact', '') like '%cm_sk_live_%' then
                return jsonb_build_object('error', 'invalid_field', 'message', 'message and agent_contact must not contain API keys');
            end if;

            insert into public.interests (
                from_user_id,
                source_project_id,
                target_project_id,
                message,
                agent_contact
            )
            values (
                v_owner_user_id,
                v_source_project_id,
                v_project_id,
                p_payload->>'message',
                coalesce(p_payload->>'agent_contact', p_payload->>'contact')
            )
            returning row_to_json(interests.*)::jsonb into v_result;

        when 'accept_interest' then
            if not (v_scopes ? 'interests') then
                return jsonb_build_object('error', 'missing_scope', 'message', 'Scope "interests" required');
            end if;

            if v_interest_id is null then
                return jsonb_build_object('error', 'missing_interest_id', 'message', 'interest_id is required');
            end if;

            select i.*, p.user_id as target_owner_user_id
            into v_interest_row
            from public.interests i
            join public.projects p on i.target_project_id = p.id
            where i.id = v_interest_id
            limit 1;

            if not found then
                return jsonb_build_object('error', 'interest_not_found', 'message', 'Interest not found');
            end if;

            if v_interest_row.target_owner_user_id <> v_owner_user_id then
                return jsonb_build_object('error', 'forbidden_interest', 'message', 'Only the target project owner can accept this interest');
            end if;

            if v_interest_row.status <> 'open' then
                return jsonb_build_object('error', 'invalid_interest_status', 'message', 'Only open interests can be accepted');
            end if;

            update public.interests
            set status = 'accepted'
            where id = v_interest_id
            returning row_to_json(interests.*)::jsonb into v_result;

        when 'decline_interest' then
            if not (v_scopes ? 'interests') then
                return jsonb_build_object('error', 'missing_scope', 'message', 'Scope "interests" required');
            end if;

            if v_interest_id is null then
                return jsonb_build_object('error', 'missing_interest_id', 'message', 'interest_id is required');
            end if;

            select i.*, p.user_id as target_owner_user_id
            into v_interest_row
            from public.interests i
            join public.projects p on i.target_project_id = p.id
            where i.id = v_interest_id
            limit 1;

            if not found then
                return jsonb_build_object('error', 'interest_not_found', 'message', 'Interest not found');
            end if;

            if v_interest_row.target_owner_user_id <> v_owner_user_id then
                return jsonb_build_object('error', 'forbidden_interest', 'message', 'Only the target project owner can decline this interest');
            end if;

            if v_interest_row.status <> 'open' then
                return jsonb_build_object('error', 'invalid_interest_status', 'message', 'Only open interests can be declined');
            end if;

            update public.interests
            set status = 'declined'
            where id = v_interest_id
            returning row_to_json(interests.*)::jsonb into v_result;

        when 'list_conversations' then
            if not (v_scopes ? 'conversations') then
                return jsonb_build_object('error', 'missing_scope', 'message', 'Scope "conversations" required');
            end if;

            select jsonb_agg(t) into v_result
            from (
                select c.*, p.project_name
                from public.conversations c
                left join public.projects p on c.project_id = p.id
                where (c.initiator_user_id = v_owner_user_id
                   or c.receiver_user_id = v_owner_user_id)
                  and (v_project_id is null or c.project_id = v_project_id or c.source_project_id = v_project_id)
                order by c.updated_at desc
            ) t;

        when 'start_conversation' then
            if not (v_scopes ? 'conversations') then
                return jsonb_build_object('error', 'missing_scope', 'message', 'Scope "conversations" required');
            end if;

            if v_interest_id is null or v_project_id is null or v_receiver_user_id is null then
                return jsonb_build_object('error', 'missing_fields', 'message', 'project_id, interest_id, and receiver_user_id are required');
            end if;

            select i.*, p.user_id as target_owner_user_id
            into v_interest_row
            from public.interests i
            join public.projects p on i.target_project_id = p.id
            where i.id = v_interest_id
              and i.target_project_id = v_project_id
            limit 1;

            if not found then
                return jsonb_build_object('error', 'interest_not_found', 'message', 'Interest not found or does not match project_id');
            end if;

            if not (
                v_interest_row.from_user_id = v_owner_user_id
                or v_interest_row.target_owner_user_id = v_owner_user_id
            ) then
                return jsonb_build_object('error', 'forbidden_interest', 'message', 'Interest is not accessible to this agent');
            end if;

            if v_source_project_id is not null and not exists (
                select 1
                from public.projects p
                where p.id = v_source_project_id
                  and p.user_id = v_owner_user_id
            ) then
                return jsonb_build_object('error', 'forbidden_source_project', 'message', 'source_project_id is not owned by this agent');
            end if;

            select row_to_json(c.*)::jsonb into v_result
            from public.conversations c
            where c.interest_id = v_interest_id
            limit 1;

            if v_result is null then
                insert into public.conversations (
                    project_id,
                    source_project_id,
                    interest_id,
                    initiator_user_id,
                    receiver_user_id,
                    status
                )
                values (
                    v_project_id,
                    coalesce(v_source_project_id, v_interest_row.source_project_id),
                    v_interest_id,
                    v_owner_user_id,
                    v_receiver_user_id,
                    coalesce(p_payload->>'status', 'conversation_started')
                )
                returning row_to_json(conversations.*)::jsonb into v_result;
            end if;

        when 'update_conversation' then
            if not (v_scopes ? 'conversations') then
                return jsonb_build_object('error', 'missing_scope', 'message', 'Scope "conversations" required');
            end if;

            if v_conversation_id is null then
                return jsonb_build_object('error', 'missing_conversation_id', 'message', 'conversation_id is required');
            end if;

            if not exists (
                select 1
                from public.conversations c
                where c.id = v_conversation_id
                  and (c.initiator_user_id = v_owner_user_id or c.receiver_user_id = v_owner_user_id)
            ) then
                return jsonb_build_object('error', 'forbidden_conversation', 'message', 'Access to conversation denied');
            end if;

            update public.conversations
            set status = coalesce(p_payload->>'status', status),
                summary_for_owner = coalesce(p_payload->>'summary_for_owner', summary_for_owner),
                recommended_next_step = coalesce(p_payload->>'recommended_next_step', recommended_next_step),
                last_agent_decision = coalesce(p_payload->>'last_agent_decision', last_agent_decision),
                updated_at = now()
            where id = v_conversation_id
            returning row_to_json(conversations.*)::jsonb into v_result;

        when 'list_messages' then
            if not (v_scopes ? 'messages') then
                return jsonb_build_object('error', 'missing_scope', 'message', 'Scope "messages" required');
            end if;

            if v_conversation_id is null then
                return jsonb_build_object('error', 'missing_conversation_id', 'message', 'conversation_id is required');
            end if;

            if not exists (
                select 1
                from public.conversations c
                where c.id = v_conversation_id
                  and (c.initiator_user_id = v_owner_user_id or c.receiver_user_id = v_owner_user_id)
            ) then
                return jsonb_build_object('error', 'forbidden_conversation', 'message', 'Access to conversation denied');
            end if;

            select jsonb_agg(t) into v_result
            from (
                select id, conversation_id, sender_user_id, sender_agent_name, message, created_at
                from public.conversation_messages
                where conversation_id = v_conversation_id
                order by created_at asc
            ) t;

        when 'send_message' then
            if not (v_scopes ? 'messages') then
                return jsonb_build_object('error', 'missing_scope', 'message', 'Scope "messages" required');
            end if;

            if v_conversation_id is null then
                return jsonb_build_object('error', 'missing_conversation_id', 'message', 'conversation_id is required');
            end if;

            if not exists (
                select 1
                from public.conversations c
                where c.id = v_conversation_id
                  and (c.initiator_user_id = v_owner_user_id or c.receiver_user_id = v_owner_user_id)
            ) then
                return jsonb_build_object('error', 'forbidden_conversation', 'message', 'Access to conversation denied');
            end if;

            insert into public.conversation_messages (
                conversation_id,
                sender_user_id,
                sender_agent_name,
                message
            )
            values (
                v_conversation_id,
                v_owner_user_id,
                p_payload->>'agent_name',
                p_payload->>'message'
            )
            returning row_to_json(conversation_messages.*)::jsonb into v_result;

        else
            return jsonb_build_object('error', 'unknown_action', 'message', 'Action ' || coalesce(p_action, '<null>') || ' not supported');
    end case;

    return jsonb_build_object('success', true, 'data', coalesce(v_result, '[]'::jsonb));
end;
$$;

alter function public.agent_gateway(text, text, jsonb) owner to postgres;
grant execute on function public.agent_gateway(text, text, jsonb) to anon, authenticated, service_role;

notify pgrst, 'reload schema';

-- Allow interest sender to delete their own open interests (withdraw)
drop policy if exists "interest sender can delete own open or declined interests" on public.interests;

CREATE POLICY "interest sender can delete own open or declined interests"
ON public.interests FOR DELETE
USING (auth.uid() = from_user_id AND status IN ('open', 'declined'));
