# Deploy Notes

## Recommended gateway SQL
Use this file for the current working Supabase RPC gateway:

- `backend/sql/gateway/AGENT_GATEWAY_CANONICAL_FIXED_SD.sql`

This is the version that was verified live to:
- expose correctly via `/rest/v1/rpc/agent_gateway`
- work with long-lived `cm_sk_live_...` keys
- bypass RLS correctly via `SECURITY DEFINER`
- avoid the earlier RPC 404 problem by using explicit typed key lookup instead of `record + select * into record`
- support project create/update/delete through the gateway

## SQL directory structure
```
backend/sql/
  schemas/         - Table definitions (deploy these first)
  gateway/         - Canonical RPC gateway (deploy after schemas)
  migrations/      - Schema upgrade scripts (for existing databases)
  archive/         - Historical debug/iteration files (NOT for production)
```

## Important
Debug/iteration SQL files have been moved to `backend/sql/archive/`.
They are useful as debugging history, not as deploy targets.

## VM-local server
`backend/agent_api_server.py` is deprecated in architecture terms.
Prefer the Supabase RPC gateway over a VM-local API server.
If the VM-local server is still kept around for experiments, it must read the service-role key only from environment variables and must never hardcode it.
