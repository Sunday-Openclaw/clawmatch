-- Fix: Enable pgcrypto and grant execute
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Re-grant just in case
GRANT EXECUTE ON FUNCTION public.agent_gateway(TEXT, TEXT, JSONB) TO anon, authenticated, service_role;
