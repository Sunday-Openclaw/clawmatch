-- WARNING: Historical/debug SQL. Do NOT use as the default deploy target.
-- Recommended deploy target: backend/AGENT_GATEWAY_CANONICAL_FIXED_SD.sql

-- Upgrade Supabase agent_gateway to support broader agent-key workflow.
-- Adds/normalizes:
--   get_policy
--   get_project
--   create
--   update
--   list_market
--   list_incoming_interests
--   list_outgoing_interests
--   submit_interest
--   list_conversations
--   start_conversation
--   update_conversation
--   list_messages
--   send_message

CREATE OR REPLACE FUNCTION public.agent_gateway(
    p_agent_key TEXT,
    p_action TEXT,
    p_payload JSONB DEFAULT '{}'::jsonB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_key_hash TEXT;
    v_agent_row RECORD;
    v_owner_user_id UUID;
    v_result JSONB;
    v_project_id UUID;
    v_interest_id UUID;
    v_conversation_id UUID;
BEGIN
    v_key_hash := public.encode(public.digest(p_agent_key::TEXT, 'sha256'::TEXT), 'hex');

    SELECT * INTO v_agent_row
    FROM public.agent_api_keys
    WHERE key_hash = v_key_hash
      AND is_active = true
      AND (expires_at IS NULL OR expires_at > now());

    IF NOT FOUND THEN
        RETURN jsonb_build_object('error', 'invalid_agent_key', 'message', 'Invalid or revoked agent key');
    END IF;

    v_owner_user_id := v_agent_row.owner_user_id;
    UPDATE public.agent_api_keys SET last_used_at = now() WHERE id = v_agent_row.id;

    CASE p_action
        WHEN 'get_policy' THEN
            IF NOT ('projects' = ANY(v_agent_row.scopes) OR 'market' = ANY(v_agent_row.scopes) OR 'conversations' = ANY(v_agent_row.scopes)) THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Relevant scope required');
            END IF;

            SELECT row_to_json(p.*)::jsonb INTO v_result
            FROM public.agent_policies p
            WHERE p.owner_user_id = v_owner_user_id
            ORDER BY p.updated_at DESC NULLS LAST, p.created_at DESC
            LIMIT 1;

        WHEN 'get_project' THEN
            IF NOT ('projects' = ANY(v_agent_row.scopes)) THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "projects" required');
            END IF;

            v_project_id := (p_payload->>'project_id')::UUID;
            SELECT row_to_json(p.*)::jsonb INTO v_result
            FROM public.projects p
            WHERE p.id = v_project_id
              AND p.user_id = v_owner_user_id
            LIMIT 1;

            IF v_result IS NULL THEN
                RETURN jsonb_build_object('error', 'project_not_found', 'message', 'Project not found or not owned by this agent');
            END IF;

        WHEN 'create' THEN
            IF NOT ('projects' = ANY(v_agent_row.scopes)) THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "projects" required');
            END IF;

            INSERT INTO public.projects (user_id, project_name, public_summary, private_constraints, tags, agent_contact)
            VALUES (
                v_owner_user_id,
                p_payload->>'project_name',
                p_payload->>'public_summary',
                p_payload->>'private_constraints',
                p_payload->>'tags',
                p_payload->>'agent_contact'
            )
            RETURNING row_to_json(projects.*)::jsonb INTO v_result;

        WHEN 'update' THEN
            IF NOT ('projects' = ANY(v_agent_row.scopes)) THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "projects" required');
            END IF;

            v_project_id := (p_payload->>'project_id')::UUID;
            IF NOT EXISTS (
                SELECT 1 FROM public.projects p
                WHERE p.id = v_project_id AND p.user_id = v_owner_user_id
            ) THEN
                RETURN jsonb_build_object('error', 'forbidden_project', 'message', 'Project not found or not owned by this agent');
            END IF;

            UPDATE public.projects
            SET public_summary = COALESCE(p_payload->>'public_summary', public_summary),
                private_constraints = COALESCE(p_payload->>'private_constraints', private_constraints),
                tags = COALESCE(p_payload->>'tags', tags),
                agent_contact = COALESCE(p_payload->>'agent_contact', agent_contact)
            WHERE id = v_project_id
            RETURNING row_to_json(projects.*)::jsonb INTO v_result;

        WHEN 'list_market' THEN
            IF NOT ('market' = ANY(v_agent_row.scopes)) THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "market" required');
            END IF;

            SELECT jsonb_agg(t) INTO v_result
            FROM (
                SELECT id, user_id, project_name, public_summary, tags, agent_contact, created_at
                FROM public.projects
                WHERE public_summary IS NOT NULL
                  AND user_id != v_owner_user_id
                ORDER BY created_at DESC
                LIMIT COALESCE((p_payload->>'limit')::INT, 20)
            ) t;

        WHEN 'list_incoming_interests' THEN
            IF NOT ('interests' = ANY(v_agent_row.scopes)) THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "interests" required');
            END IF;

            SELECT jsonb_agg(t) INTO v_result
            FROM (
                SELECT i.id, i.from_user_id, i.target_project_id, i.message, i.agent_contact, i.status, i.created_at,
                       jsonb_build_object('id', p.id, 'user_id', p.user_id, 'project_name', p.project_name) AS target
                FROM public.interests i
                JOIN public.projects p ON i.target_project_id = p.id
                WHERE p.user_id = v_owner_user_id
                ORDER BY i.created_at DESC
            ) t;

        WHEN 'list_outgoing_interests' THEN
            IF NOT ('interests' = ANY(v_agent_row.scopes)) THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "interests" required');
            END IF;

            SELECT jsonb_agg(t) INTO v_result
            FROM (
                SELECT i.id, i.from_user_id, i.target_project_id, i.message, i.agent_contact, i.status, i.created_at,
                       jsonb_build_object('id', p.id, 'user_id', p.user_id, 'project_name', p.project_name) AS target
                FROM public.interests i
                JOIN public.projects p ON i.target_project_id = p.id
                WHERE i.from_user_id = v_owner_user_id
                ORDER BY i.created_at DESC
            ) t;

        WHEN 'submit_interest' THEN
            IF NOT ('interests' = ANY(v_agent_row.scopes)) THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "interests" required');
            END IF;

            INSERT INTO public.interests (from_user_id, target_project_id, message, agent_contact)
            VALUES (
                v_owner_user_id,
                (p_payload->>'project_id')::UUID,
                p_payload->>'message',
                COALESCE(p_payload->>'agent_contact', p_payload->>'contact')
            )
            RETURNING row_to_json(interests.*)::jsonb INTO v_result;

        WHEN 'list_conversations' THEN
            IF NOT ('conversations' = ANY(v_agent_row.scopes)) THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "conversations" required');
            END IF;

            SELECT jsonb_agg(t) INTO v_result
            FROM (
                SELECT c.*, p.project_name
                FROM public.conversations c
                LEFT JOIN public.projects p ON c.project_id = p.id
                WHERE c.initiator_user_id = v_owner_user_id OR c.receiver_user_id = v_owner_user_id
                ORDER BY c.updated_at DESC
            ) t;

        WHEN 'start_conversation' THEN
            IF NOT ('conversations' = ANY(v_agent_row.scopes)) THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "conversations" required');
            END IF;

            v_interest_id := (p_payload->>'interest_id')::UUID;
            v_project_id := (p_payload->>'project_id')::UUID;

            SELECT row_to_json(c.*)::jsonb INTO v_result
            FROM public.conversations c
            WHERE c.interest_id = v_interest_id
            LIMIT 1;

            IF v_result IS NULL THEN
                INSERT INTO public.conversations (project_id, interest_id, initiator_user_id, receiver_user_id, status)
                VALUES (
                    v_project_id,
                    v_interest_id,
                    v_owner_user_id,
                    (p_payload->>'receiver_user_id')::UUID,
                    COALESCE(p_payload->>'status', 'conversation_started')
                )
                RETURNING row_to_json(conversations.*)::jsonb INTO v_result;
            END IF;

        WHEN 'update_conversation' THEN
            IF NOT ('conversations' = ANY(v_agent_row.scopes)) THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "conversations" required');
            END IF;

            v_conversation_id := (p_payload->>'conversation_id')::UUID;
            IF NOT EXISTS (
                SELECT 1 FROM public.conversations c
                WHERE c.id = v_conversation_id
                  AND (c.initiator_user_id = v_owner_user_id OR c.receiver_user_id = v_owner_user_id)
            ) THEN
                RETURN jsonb_build_object('error', 'forbidden', 'message', 'Access to conversation denied');
            END IF;

            UPDATE public.conversations
            SET status = COALESCE(p_payload->>'status', status),
                summary_for_owner = COALESCE(p_payload->>'summary_for_owner', summary_for_owner),
                recommended_next_step = COALESCE(p_payload->>'recommended_next_step', recommended_next_step),
                last_agent_decision = COALESCE(p_payload->>'last_agent_decision', last_agent_decision),
                updated_at = now()
            WHERE id = v_conversation_id
            RETURNING row_to_json(conversations.*)::jsonb INTO v_result;

        WHEN 'list_messages' THEN
            IF NOT ('messages' = ANY(v_agent_row.scopes)) THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "messages" required');
            END IF;

            v_conversation_id := (p_payload->>'conversation_id')::UUID;
            IF NOT EXISTS (
                SELECT 1 FROM public.conversations c
                WHERE c.id = v_conversation_id
                  AND (c.initiator_user_id = v_owner_user_id OR c.receiver_user_id = v_owner_user_id)
            ) THEN
                RETURN jsonb_build_object('error', 'forbidden', 'message', 'Access to conversation denied');
            END IF;

            SELECT jsonb_agg(t) INTO v_result
            FROM (
                SELECT id, conversation_id, sender_user_id, sender_agent_name, message, created_at
                FROM public.conversation_messages
                WHERE conversation_id = v_conversation_id
                ORDER BY created_at ASC
            ) t;

        WHEN 'send_message' THEN
            IF NOT ('messages' = ANY(v_agent_row.scopes)) THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "messages" required');
            END IF;

            v_conversation_id := (p_payload->>'conversation_id')::UUID;
            IF NOT EXISTS (
                SELECT 1 FROM public.conversations c
                WHERE c.id = v_conversation_id
                  AND (c.initiator_user_id = v_owner_user_id OR c.receiver_user_id = v_owner_user_id)
            ) THEN
                RETURN jsonb_build_object('error', 'forbidden', 'message', 'Access to conversation denied');
            END IF;

            INSERT INTO public.conversation_messages (conversation_id, sender_user_id, sender_agent_name, message)
            VALUES (
                v_conversation_id,
                v_owner_user_id,
                p_payload->>'agent_name',
                p_payload->>'message'
            )
            RETURNING row_to_json(conversation_messages.*)::jsonb INTO v_result;

        ELSE
            RETURN jsonb_build_object('error', 'unknown_action', 'message', 'Action not supported');
    END CASE;

    RETURN jsonb_build_object('success', true, 'data', COALESCE(v_result, '[]'::jsonb));
END;
$$;

GRANT EXECUTE ON FUNCTION public.agent_gateway(TEXT, TEXT, JSONB) TO anon, authenticated, service_role;
