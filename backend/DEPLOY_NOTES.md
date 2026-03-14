# Deploy Notes

## Recommended gateway SQL
Use this file for the current working Supabase RPC gateway:

- `backend/AGENT_GATEWAY_CANONICAL_FIXED_SD.sql`

This is the version that was verified live to:
- expose correctly via `/rest/v1/rpc/agent_gateway`
- work with long-lived `cm_sk_live_...` keys
- bypass RLS correctly via `SECURITY DEFINER`
- avoid the earlier RPC 404 problem by using explicit typed key lookup instead of `record + select * into record`

## Important
Do **not** treat these as the primary deploy target:
- `backend/archive/AGENT_GATEWAY_CANONICAL.sql`
- `backend/archive/AGENT_GATEWAY_CANONICAL_LITE.sql`
- `backend/archive/AGENT_GATEWAY_CANONICAL_FIXED.sql` (fixes RPC exposure but not RLS)
- `backend/archive/AGENT_GATEWAY_STAGE1.sql` ... `backend/archive/AGENT_GATEWAY_STAGE5.sql`

They are useful as debugging history, not as the canonical production deploy path.

## VM-local server
`backend/agent_api_server.py` is deprecated in architecture terms.
Prefer the Supabase RPC gateway over a VM-local API server.
If the VM-local server is still kept around for experiments, it must read the service-role key only from environment variables and must never hardcode it.
