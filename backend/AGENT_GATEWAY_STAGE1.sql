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

        else
            return jsonb_build_object('error', 'unknown_action', 'message', 'Action ' || coalesce(p_action, '<null>') || ' not supported in stage1');
    end case;

    return jsonb_build_object('success', true, 'data', coalesce(v_result, '[]'::jsonb));
end;
$$;

grant execute on function public.agent_gateway(text, text, jsonb) to anon, authenticated, service_role;

notify pgrst, 'reload schema';
