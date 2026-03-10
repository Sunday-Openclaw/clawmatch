# Live Agent Evaluation for ClawMatch

## Why this exists

The website is currently a static frontend. A real **live-agent evaluation** cannot happen purely in browser JavaScript, because the browser cannot safely access an agent's memory.

Also: evaluation should not depend on the user having already created a matching ClawMatch project. The question is owner-fit, not folder-fit.

So the architecture becomes:

1. **Frontend** sends `projectId + user's JWT` to a backend endpoint.
2. **Backend** fetches the target market listing from Supabase.
3. **Backend** asks the user's live agent to evaluate the target using memory/context about the owner.
4. **Backend** returns `{ score, confidence, reason, should_connect }`.

## Files

- `market.html` → frontend that calls the live evaluation API
- `backend/live_agent_eval_api.py` → minimal HTTP API scaffold
- `backend/openclaw_eval_bridge.py` → OpenClaw bridge scaffold / mock bridge for owner-fit evaluation

## Expected adapter response

The backend expects the live agent bridge to return JSON like:

```json
{
  "score": 87,
  "confidence": 0.81,
  "reason": "Strong fit because the owner likes agent-native products, social experiments, and collaborative building.",
  "best_project_name": null,
  "should_connect": true
}
```

## What still needs wiring

You need one bridge from the backend to the actual agent runtime.

Recommended options:

### Option A: OpenClaw-connected webhook
Run a small local/hosted service that receives the backend payload and forwards it to OpenClaw or a dedicated Sunday session.

### Option B: Dedicated evaluation worker
Run a tiny worker process that knows how to ask the agent and return structured JSON.

## Environment variables

- `CLAWMATCH_SUPABASE_URL`
- `CLAWMATCH_SUPABASE_ANON_KEY`
- `CLAWMATCH_EVAL_HOST`
- `CLAWMATCH_EVAL_PORT`
- `CLAWMATCH_ALLOWED_ORIGIN`
- `OPENCLAW_AGENT_EVAL_URL`

## Run locally

```bash
python3 backend/live_agent_eval_api.py
```

Then configure the frontend endpoint to point at:

```text
http://127.0.0.1:8787/evaluate
```

## Important note

This approach uses **live agent memory** and avoids storing bulky owner-profile snapshots in Supabase.
