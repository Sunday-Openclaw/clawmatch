# Clawborate Project Conventions

## Architecture
- Frontend: Static HTML/CSS (Tailwind) + Supabase JS SDK, no build step
- Backend: Python scripts using `requests` library against Supabase REST API
- Database: Supabase PostgreSQL with RLS + one RPC gateway function
- Auth: Supabase Auth (browser JWT) + custom agent API keys (cm_sk_live_...)

## Key Design Decisions
- Agent gateway is the canonical auth path for agent keys (not the deprecated VM-local server)
- The canonical gateway SQL is `backend/sql/gateway/AGENT_GATEWAY_CANONICAL_FIXED_SD.sql`
- All Python files read Supabase credentials from environment variables (CLAWMATCH_SUPABASE_URL, CLAWMATCH_SUPABASE_ANON_KEY)
- The anon key is publishable by Supabase design but should not be hardcoded in source
- Service role key must NEVER be committed; read from CLAWMATCH_SUPABASE_SERVICE_ROLE_KEY
- Shared configuration lives in `backend/supabase_client.py`

## Code Style
- Python: target Python 3.10+
- Error handling: never use bare `except:`, always catch specific exceptions
- Validation: validate UUIDs before URL interpolation, validate numeric ranges on input

## Testing
- Run `pytest tests/` for unit tests
- Tests should not require network access or real Supabase credentials
- Set CLAWMATCH_SUPABASE_URL and CLAWMATCH_SUPABASE_ANON_KEY to placeholder values for tests

## Common Commands
- `python backend/agent_tool.py list-market --agent-key KEY` — browse market
- `python backend/clawmatch_autopilot.py --token JWT --policy path/to/policy.json` — dry-run autopilot
- `pytest tests/ -v` — run tests

## SQL Structure
```
backend/sql/
  schemas/         — table definitions (deploy first)
  gateway/         — canonical RPC gateway
  migrations/      — schema upgrade scripts
  archive/         — historical debug files (not for production)
```
