# VM-local API server status

`backend/agent_api_server.py` is deprecated as the primary architecture.

## Recommended path
Use the Supabase RPC gateway instead:
- `backend/AGENT_GATEWAY_CANONICAL_FIXED_SD.sql`

## Why
The project direction has moved away from running a VM-local API key server and toward a single Supabase RPC gateway for long-lived `cm_sk_live_...` agent keys.

## If you keep the local server around
Treat it as experimental/debug-only:
- do not hardcode service-role tokens
- read secrets only from environment variables
- do not treat it as the production/default path
