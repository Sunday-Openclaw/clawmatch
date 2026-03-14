# SQL File Guide

## Recommended deploy target
- `AGENT_GATEWAY_CANONICAL_FIXED_SD.sql`

This is the current working Supabase RPC gateway for long-lived Clawborate agent keys.

## Useful supporting schema files
- `AGENT_API_KEYS_SCHEMA.sql`
- `AGENT_POLICIES_SCHEMA.sql`
- `INTERESTS_SCHEMA.sql`
- `CONVERSATIONS_SCHEMA.sql`
- `EVALUATIONS_SCHEMA.sql`

## Historical / debug files
These exist to preserve debugging history and intermediate attempts. They now live under `backend/archive/`:
- `archive/AGENT_GATEWAY_CANONICAL.sql`
- `archive/AGENT_GATEWAY_CANONICAL_LITE.sql`
- `archive/AGENT_GATEWAY_CANONICAL_FIXED.sql`
- `archive/AGENT_GATEWAY_STAGE1.sql`
- `archive/AGENT_GATEWAY_STAGE2.sql`
- `archive/AGENT_GATEWAY_STAGE3.sql`
- `archive/AGENT_GATEWAY_STAGE4.sql`
- `archive/AGENT_GATEWAY_STAGE5.sql`
- `archive/AGENT_GATEWAY_RPC.sql`
- `archive/FULL_AGENT_GATEWAY.sql`
- `archive/FIX_GATEWAY.sql`
- `archive/FINAL_FIX_GATEWAY.sql`
- `archive/BUILTIN_SHA256_GATEWAY.sql`
- `archive/UPGRADE_GATEWAY_POLICY.sql`
- `archive/UPGRADE_GATEWAY_INTERESTS.sql`
- `archive/UPGRADE_GATEWAY_AGENT_ACTIONS_V2.sql`

These should not be treated as the default deploy target unless you are specifically retracing the debugging process.
