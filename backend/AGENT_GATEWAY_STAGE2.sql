drop function if exists public.agent_gateway(text, text, jsonb);

create or replace function public.agent_gateway(
    p_agent_key text,
    p_action text,
    p_payload jsonb default '{}'::jsonb
)
returns jsonb
language plpgsql
as $$
declare
    v_key_hash text;
    v_key_id uuid;
    v_owner_user_id uuid;
    v_scopes jsonb;
    v_result jsonb;
    v_limit int;
    v_project_id uuid;
begin
    v_key_hash := encode(digest(p_agent_key::text, 'sha256'::text), 'hex');

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

    v_limit := greatest(1, least(coalesce(nullif(p_payload->>'limit', '')::int, 20), 100));
    v_project_id := nullif(p_payload->>'project_id', '')::uuid;

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

            update public.projects
            set public_summary = coalesce(p_payload->>'public_summary', public_summary),
                private_constraints = coalesce(p_payload->>'private_constraints', private_constraints),
                tags = coalesce(p_payload->>'tags', tags),
                agent_contact = coalesce(p_payload->>'agent_contact', agent_contact)
            where id = v_project_id
            returning row_to_json(projects.*)::jsonb into v_result;

        else
            return jsonb_build_object('error', 'unknown_action', 'message', 'Action ' || coalesce(p_action, '<null>') || ' not supported in stage2');
    end case;

    return jsonb_build_object('success', true, 'data', coalesce(v_result, '[]'::jsonb));
end;
$$;

grant execute on function public.agent_gateway(text, text, jsonb) to anon, authenticated, service_role;

notify pgrst, 'reload schema';
