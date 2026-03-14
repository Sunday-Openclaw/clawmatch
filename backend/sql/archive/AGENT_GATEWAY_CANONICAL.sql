-- WARNING: Historical/debug SQL. Do NOT use as the default deploy target.
-- Recommended deploy target: backend/AGENT_GATEWAY_CANONICAL_FIXED_SD.sql

-- Canonical Supabase RPC gateway for long-lived Clawborate agent keys.
--
-- Product direction:
-- - agent keys (cm_sk_live_...) are NOT Supabase JWTs
-- - agents authenticate only via this RPC
-- - no VM-local agent API server required
--
-- Canonical action names:
--   get_policy
--   get_project
--   create_project   (legacy alias: create)
--   update_project   (legacy alias: update)
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
    p_payload JSONB DEFAULT '{}'::JSONB
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
    v_receiver_user_id UUID;
    v_limit INT;
    v_target_project_owner UUID;
    v_interest_row RECORD;
BEGIN
    v_key_hash := public.encode(public.digest(p_agent_key::TEXT, 'sha256'::TEXT), 'hex');

    SELECT * INTO v_agent_row
    FROM public.agent_api_keys
    WHERE key_hash = v_key_hash
      AND is_active = true
      AND (expires_at IS NULL OR expires_at > now())
    LIMIT 1;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('error', 'invalid_agent_key', 'message', 'Invalid or revoked agent key');
    END IF;

    v_owner_user_id := v_agent_row.owner_user_id;

    UPDATE public.agent_api_keys
    SET last_used_at = now()
    WHERE id = v_agent_row.id;

    v_project_id := NULLIF(p_payload->>'project_id', '')::UUID;
    v_interest_id := NULLIF(p_payload->>'interest_id', '')::UUID;
    v_conversation_id := NULLIF(p_payload->>'conversation_id', '')::UUID;
    v_receiver_user_id := NULLIF(p_payload->>'receiver_user_id', '')::UUID;
    v_limit := GREATEST(1, LEAST(COALESCE(NULLIF(p_payload->>'limit', '')::INT, 20), 100));

    CASE p_action
        WHEN 'get_policy' THEN
            IF NOT (
                v_agent_row.scopes ? 'policy'
                OR v_agent_row.scopes ? 'projects'
                OR v_agent_row.scopes ? 'market'
                OR v_agent_row.scopes ? 'conversations'
            ) THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "policy" (or compatible legacy scope) required');
            END IF;

            SELECT row_to_json(p.*)::JSONB INTO v_result
            FROM public.agent_policies p
            WHERE p.owner_user_id = v_owner_user_id
            ORDER BY p.updated_at DESC NULLS LAST, p.created_at DESC
            LIMIT 1;

        WHEN 'get_project' THEN
            IF NOT (v_agent_row.scopes ? 'projects') THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "projects" required');
            END IF;

            IF v_project_id IS NULL THEN
                RETURN jsonb_build_object('error', 'missing_project_id', 'message', 'project_id is required');
            END IF;

            SELECT row_to_json(p.*)::JSONB INTO v_result
            FROM public.projects p
            WHERE p.id = v_project_id
              AND p.user_id = v_owner_user_id
            LIMIT 1;

            IF v_result IS NULL THEN
                RETURN jsonb_build_object('error', 'project_not_found', 'message', 'Project not found or not owned by this agent');
            END IF;

        WHEN 'create_project', 'create' THEN
            IF NOT (v_agent_row.scopes ? 'projects') THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "projects" required');
            END IF;

            INSERT INTO public.projects (
                user_id,
                project_name,
                public_summary,
                private_constraints,
                tags,
                agent_contact
            )
            VALUES (
                v_owner_user_id,
                p_payload->>'project_name',
                p_payload->>'public_summary',
                p_payload->>'private_constraints',
                p_payload->>'tags',
                p_payload->>'agent_contact'
            )
            RETURNING row_to_json(projects.*)::JSONB INTO v_result;

        WHEN 'update_project', 'update' THEN
            IF NOT (v_agent_row.scopes ? 'projects') THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "projects" required');
            END IF;

            IF v_project_id IS NULL THEN
                RETURN jsonb_build_object('error', 'missing_project_id', 'message', 'project_id is required');
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM public.projects p
                WHERE p.id = v_project_id
                  AND p.user_id = v_owner_user_id
            ) THEN
                RETURN jsonb_build_object('error', 'forbidden_project', 'message', 'Project not found or not owned by this agent');
            END IF;

            UPDATE public.projects
            SET public_summary = COALESCE(p_payload->>'public_summary', public_summary),
                private_constraints = COALESCE(p_payload->>'private_constraints', private_constraints),
                tags = COALESCE(p_payload->>'tags', tags),
                agent_contact = COALESCE(p_payload->>'agent_contact', agent_contact)
            WHERE id = v_project_id
            RETURNING row_to_json(projects.*)::JSONB INTO v_result;

        WHEN 'list_market' THEN
            IF NOT (v_agent_row.scopes ? 'market') THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "market" required');
            END IF;

            SELECT jsonb_agg(t) INTO v_result
            FROM (
                SELECT id, user_id, project_name, public_summary, tags, agent_contact, created_at
                FROM public.projects
                WHERE public_summary IS NOT NULL
                  AND user_id <> v_owner_user_id
                ORDER BY created_at DESC
                LIMIT v_limit
            ) t;

        WHEN 'list_incoming_interests' THEN
            IF NOT (v_agent_row.scopes ? 'interests') THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "interests" required');
            END IF;

            SELECT jsonb_agg(t) INTO v_result
            FROM (
                SELECT
                    i.id,
                    i.from_user_id,
                    i.target_project_id,
                    i.message,
                    i.agent_contact,
                    i.status,
                    i.created_at,
                    jsonb_build_object(
                        'id', p.id,
                        'user_id', p.user_id,
                        'project_name', p.project_name
                    ) AS target
                FROM public.interests i
                JOIN public.projects p ON i.target_project_id = p.id
                WHERE p.user_id = v_owner_user_id
                ORDER BY i.created_at DESC
            ) t;

        WHEN 'list_outgoing_interests' THEN
            IF NOT (v_agent_row.scopes ? 'interests') THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "interests" required');
            END IF;

            SELECT jsonb_agg(t) INTO v_result
            FROM (
                SELECT
                    i.id,
                    i.from_user_id,
                    i.target_project_id,
                    i.message,
                    i.agent_contact,
                    i.status,
                    i.created_at,
                    jsonb_build_object(
                        'id', p.id,
                        'user_id', p.user_id,
                        'project_name', p.project_name
                    ) AS target
                FROM public.interests i
                JOIN public.projects p ON i.target_project_id = p.id
                WHERE i.from_user_id = v_owner_user_id
                ORDER BY i.created_at DESC
            ) t;

        WHEN 'submit_interest' THEN
            IF NOT (v_agent_row.scopes ? 'interests') THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "interests" required');
            END IF;

            IF v_project_id IS NULL THEN
                RETURN jsonb_build_object('error', 'missing_project_id', 'message', 'project_id is required');
            END IF;

            SELECT p.user_id INTO v_target_project_owner
            FROM public.projects p
            WHERE p.id = v_project_id
            LIMIT 1;

            IF v_target_project_owner IS NULL THEN
                RETURN jsonb_build_object('error', 'project_not_found', 'message', 'Target project not found');
            END IF;

            IF v_target_project_owner = v_owner_user_id THEN
                RETURN jsonb_build_object('error', 'cannot_interest_own_project', 'message', 'Cannot submit interest to your own project');
            END IF;

            IF EXISTS (
                SELECT 1
                FROM public.interests i
                WHERE i.from_user_id = v_owner_user_id
                  AND i.target_project_id = v_project_id
                  AND i.status IN ('open', 'accepted')
            ) THEN
                RETURN jsonb_build_object('error', 'duplicate_interest', 'message', 'An open or accepted interest already exists for this project');
            END IF;

            INSERT INTO public.interests (
                from_user_id,
                target_project_id,
                message,
                agent_contact
            )
            VALUES (
                v_owner_user_id,
                v_project_id,
                p_payload->>'message',
                COALESCE(p_payload->>'agent_contact', p_payload->>'contact')
            )
            RETURNING row_to_json(interests.*)::JSONB INTO v_result;

        WHEN 'list_conversations' THEN
            IF NOT (v_agent_row.scopes ? 'conversations') THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "conversations" required');
            END IF;

            SELECT jsonb_agg(t) INTO v_result
            FROM (
                SELECT c.*, p.project_name
                FROM public.conversations c
                LEFT JOIN public.projects p ON c.project_id = p.id
                WHERE c.initiator_user_id = v_owner_user_id
                   OR c.receiver_user_id = v_owner_user_id
                ORDER BY c.updated_at DESC
            ) t;

        WHEN 'start_conversation' THEN
            IF NOT (v_agent_row.scopes ? 'conversations') THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "conversations" required');
            END IF;

            IF v_interest_id IS NULL OR v_project_id IS NULL OR v_receiver_user_id IS NULL THEN
                RETURN jsonb_build_object('error', 'missing_fields', 'message', 'project_id, interest_id, and receiver_user_id are required');
            END IF;

            SELECT i.*, p.user_id AS target_owner_user_id
            INTO v_interest_row
            FROM public.interests i
            JOIN public.projects p ON i.target_project_id = p.id
            WHERE i.id = v_interest_id
              AND i.target_project_id = v_project_id
            LIMIT 1;

            IF NOT FOUND THEN
                RETURN jsonb_build_object('error', 'interest_not_found', 'message', 'Interest not found or does not match project_id');
            END IF;

            IF NOT (
                v_interest_row.from_user_id = v_owner_user_id
                OR v_interest_row.target_owner_user_id = v_owner_user_id
            ) THEN
                RETURN jsonb_build_object('error', 'forbidden_interest', 'message', 'Interest is not accessible to this agent');
            END IF;

            SELECT row_to_json(c.*)::JSONB INTO v_result
            FROM public.conversations c
            WHERE c.interest_id = v_interest_id
            LIMIT 1;

            IF v_result IS NULL THEN
                INSERT INTO public.conversations (
                    project_id,
                    interest_id,
                    initiator_user_id,
                    receiver_user_id,
                    status
                )
                VALUES (
                    v_project_id,
                    v_interest_id,
                    v_owner_user_id,
                    v_receiver_user_id,
                    COALESCE(p_payload->>'status', 'conversation_started')
                )
                RETURNING row_to_json(conversations.*)::JSONB INTO v_result;
            END IF;

        WHEN 'update_conversation' THEN
            IF NOT (v_agent_row.scopes ? 'conversations') THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "conversations" required');
            END IF;

            IF v_conversation_id IS NULL THEN
                RETURN jsonb_build_object('error', 'missing_conversation_id', 'message', 'conversation_id is required');
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM public.conversations c
                WHERE c.id = v_conversation_id
                  AND (c.initiator_user_id = v_owner_user_id OR c.receiver_user_id = v_owner_user_id)
            ) THEN
                RETURN jsonb_build_object('error', 'forbidden_conversation', 'message', 'Access to conversation denied');
            END IF;

            UPDATE public.conversations
            SET status = COALESCE(p_payload->>'status', status),
                summary_for_owner = COALESCE(p_payload->>'summary_for_owner', summary_for_owner),
                recommended_next_step = COALESCE(p_payload->>'recommended_next_step', recommended_next_step),
                last_agent_decision = COALESCE(p_payload->>'last_agent_decision', last_agent_decision),
                updated_at = now()
            WHERE id = v_conversation_id
            RETURNING row_to_json(conversations.*)::JSONB INTO v_result;

        WHEN 'list_messages' THEN
            IF NOT (v_agent_row.scopes ? 'messages') THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "messages" required');
            END IF;

            IF v_conversation_id IS NULL THEN
                RETURN jsonb_build_object('error', 'missing_conversation_id', 'message', 'conversation_id is required');
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM public.conversations c
                WHERE c.id = v_conversation_id
                  AND (c.initiator_user_id = v_owner_user_id OR c.receiver_user_id = v_owner_user_id)
            ) THEN
                RETURN jsonb_build_object('error', 'forbidden_conversation', 'message', 'Access to conversation denied');
            END IF;

            SELECT jsonb_agg(t) INTO v_result
            FROM (
                SELECT id, conversation_id, sender_user_id, sender_agent_name, message, created_at
                FROM public.conversation_messages
                WHERE conversation_id = v_conversation_id
                ORDER BY created_at ASC
            ) t;

        WHEN 'send_message' THEN
            IF NOT (v_agent_row.scopes ? 'messages') THEN
                RETURN jsonb_build_object('error', 'missing_scope', 'message', 'Scope "messages" required');
            END IF;

            IF v_conversation_id IS NULL THEN
                RETURN jsonb_build_object('error', 'missing_conversation_id', 'message', 'conversation_id is required');
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM public.conversations c
                WHERE c.id = v_conversation_id
                  AND (c.initiator_user_id = v_owner_user_id OR c.receiver_user_id = v_owner_user_id)
            ) THEN
                RETURN jsonb_build_object('error', 'forbidden_conversation', 'message', 'Access to conversation denied');
            END IF;

            INSERT INTO public.conversation_messages (
                conversation_id,
                sender_user_id,
                sender_agent_name,
                message
            )
            VALUES (
                v_conversation_id,
                v_owner_user_id,
                p_payload->>'agent_name',
                p_payload->>'message'
            )
            RETURNING row_to_json(conversation_messages.*)::JSONB INTO v_result;

        ELSE
            RETURN jsonb_build_object('error', 'unknown_action', 'message', 'Action ' || COALESCE(p_action, '<null>') || ' not supported');
    END CASE;

    RETURN jsonb_build_object('success', true, 'data', COALESCE(v_result, '[]'::JSONB));
END;
$$;

GRANT EXECUTE ON FUNCTION public.agent_gateway(TEXT, TEXT, JSONB) TO anon, authenticated, service_role;
