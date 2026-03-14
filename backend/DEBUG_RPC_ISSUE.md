# Debugging `agent_gateway` RPC exposure in Supabase

## Goal
Move Clawborate to a Supabase RPC gateway model for long-lived agent keys (`cm_sk_live_...`) and stop relying on a VM-local API server.

## Current symptom
`public.agent_gateway(text, text, jsonb)` exists in Postgres, but:

- `POST /rest/v1/rpc/agent_gateway` returns **404 Not Found**

At the same time, a minimal control RPC works fine:

- `POST /rest/v1/rpc/hello_rpc` returns **200**

So this is **not** a global Supabase RPC outage.

## What we verified

### Database-side existence
These checks succeed:

```sql
select
  n.nspname as schema,
  p.proname as function_name,
  pg_get_function_identity_arguments(p.oid) as args
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where p.proname = 'agent_gateway';
```

Result indicates:
- schema: `public`
- function: `agent_gateway`
- args: `p_agent_key text, p_action text, p_payload jsonb`

### Hash helpers exist
This works:

```sql
select encode(digest('test', 'sha256'), 'hex');
```

### Grants exist
`anon`, `authenticated`, and `service_role` all have EXECUTE on:

```sql
public.agent_gateway(text, text, jsonb)
```

### Schema reload was attempted
This was executed multiple times:

```sql
notify pgrst, 'reload schema';
```

## Key observation
A **minimal** replacement for `public.agent_gateway(text,text,jsonb)` can be exposed and called successfully.

Example minimal function:

```sql
drop function if exists public.agent_gateway(text, text, jsonb);

create or replace function public.agent_gateway(
  p_agent_key text,
  p_action text,
  p_payload jsonb
)
returns jsonb
language sql
as '
  select jsonb_build_object(
    ''ok'', true,
    ''agent_key_prefix'', left(p_agent_key, 12),
    ''action'', p_action,
    ''payload'', coalesce(p_payload, ''{}''::jsonb)
  );
';

grant execute on function public.agent_gateway(text, text, jsonb) to anon, authenticated, service_role;
notify pgrst, 'reload schema';
```

This returns **200** through REST.

## Therefore
The issue is **not**:
- function name alone
- function signature alone
- grants alone
- RPC subsystem globally

The issue appears to be caused by something in the **complex function body / definition style** of the real gateway.

## Candidate culprits
Still under investigation:
- PL/pgSQL body complexity
- `record` variables / `select * into record`
- specific query fragments inside the function
- some combination of JSONB usage + PL/pgSQL causing PostgREST to not expose the RPC

## Current status / recommended deploy target
The issue is now understood well enough to recommend a deploy target.

### Recommended SQL to deploy
- `backend/AGENT_GATEWAY_CANONICAL_FIXED_SD.sql`

Why this version:
- avoids `record` + `select * into record` during `agent_api_keys` auth lookup (which correlated with RPC 404 exposure failures)
- uses explicit typed column selection for key lookup
- uses `SECURITY DEFINER`, which is required so the gateway can read `agent_api_keys` under RLS
- has been live-tested successfully for:
  - list-market
  - get-policy
  - get-project
  - create/update project
  - list incoming/outgoing interests
  - list conversations/messages
  - send-message
  - start-conversation

### Debug-only files
These are useful for understanding the investigation, but are not the primary deploy target. They now live under `backend/archive/`:
- `backend/archive/AGENT_GATEWAY_CANONICAL.sql`
- `backend/archive/AGENT_GATEWAY_CANONICAL_LITE.sql`
- `backend/archive/AGENT_GATEWAY_STAGE1.sql` ... `backend/archive/AGENT_GATEWAY_STAGE5.sql`

## Files in this repo relevant to the issue
- `backend/agent_tool.py` — Python RPC client with action alias fallback
- `backend/AGENT_GATEWAY_CANONICAL_FIXED_SD.sql` — current working / recommended gateway SQL
- `backend/AGENT_GATEWAY_CANONICAL.sql` — earlier full canonical draft
- `backend/AGENT_GATEWAY_CANONICAL_LITE.sql` — earlier simplified canonical draft

## Desired outcome
A single Supabase RPC gateway for agent keys that supports:
- get_policy
- get_project
- create/update project
- list_market
- list incoming/outgoing interests
- submit_interest
- list/start/update conversations
- list/send messages

without requiring a VM-local API server.
