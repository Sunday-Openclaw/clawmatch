-- WARNING: Historical/debug SQL. Do NOT use as the default deploy target.
-- Recommended deploy target: backend/AGENT_GATEWAY_CANONICAL_FIXED_SD.sql

-- Fix: Enable pgcrypto and grant execute
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Re-grant just in case
GRANT EXECUTE ON FUNCTION public.agent_gateway(TEXT, TEXT, JSONB) TO anon, authenticated, service_role;
