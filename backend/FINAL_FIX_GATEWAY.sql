-- Ultimate attempt to fix the extension and function visibility
-- 1. Ensure extension is in public
CREATE EXTENSION IF NOT EXISTS pgcrypto SCHEMA public;

-- 2. Redefine the function with an explicit search path and type casting
CREATE OR REPLACE FUNCTION public.agent_gateway(
    p_agent_key TEXT,
    p_action TEXT,
    p_payload JSONB DEFAULT '{}'::jsonB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_key_hash TEXT;
    v_agent_row RECORD;
    v_owner_user_id UUID;
    v_result JSONB;
BEGIN
    -- Use explicit public.digest and cast arguments to TEXT
    v_key_hash := public.encode(public.digest(p_agent_key::TEXT, 'sha256'::TEXT), 'hex');

    -- Authenticate the key
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
        WHEN 'list_market' THEN
            SELECT jsonb_agg(t) INTO v_result
            FROM (
                SELECT id, user_id, project_name, public_summary, tags, agent_contact, created_at
                FROM public.projects
                WHERE public_summary IS NOT NULL AND user_id != v_owner_user_id
                ORDER BY created_at DESC
                LIMIT COALESCE((p_payload->>'limit')::INT, 20)
            ) t;

        WHEN 'list_conversations' THEN
            SELECT jsonb_agg(t) INTO v_result
            FROM (
                SELECT c.*, p.project_name
                FROM public.conversations c
                LEFT JOIN public.projects p ON c.project_id = p.id
                WHERE c.initiator_user_id = v_owner_user_id OR c.receiver_user_id = v_owner_user_id
                ORDER BY c.updated_at DESC
            ) t;

        WHEN 'list_messages' THEN
            SELECT jsonb_agg(t) INTO v_result
            FROM (
                SELECT id, conversation_id, sender_user_id, sender_agent_name, message, created_at
                FROM public.conversation_messages
                WHERE conversation_id = (p_payload->>'conversation_id')::UUID
                ORDER BY created_at ASC
            ) t;

        WHEN 'send_message' THEN
            INSERT INTO public.conversation_messages (conversation_id, sender_user_id, sender_agent_name, message)
            VALUES (
                (p_payload->>'conversation_id')::UUID,
                v_owner_user_id,
                p_payload->>'agent_name',
                p_payload->>'message'
            )
            RETURNING row_to_json(conversation_messages.*)::jsonb INTO v_result;

        WHEN 'submit_interest' THEN
            INSERT INTO public.interests (from_user_id, target_project_id, message, agent_contact)
            VALUES (
                v_owner_user_id,
                (p_payload->>'project_id')::UUID,
                p_payload->>'message',
                p_payload->>'contact'
            )
            RETURNING row_to_json(interests.*)::jsonb INTO v_result;

        ELSE
            RETURN jsonb_build_object('error', 'unknown_action', 'message', 'Action not supported');
    END CASE;

    RETURN jsonb_build_object('success', true, 'data', COALESCE(v_result, '[]'::jsonb));
END;
$$;

GRANT EXECUTE ON FUNCTION public.agent_gateway(TEXT, TEXT, JSONB) TO anon, authenticated, service_role;
