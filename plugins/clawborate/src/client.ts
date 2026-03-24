/**
 * client.ts — Types, configuration, errors, and Clawborate RPC gateway client.
 *
 * Merged from: types.ts + errors.ts + config.ts + clawborate-client.ts
 */

import { readFile } from "fs/promises";
import { homedir } from "os";
import { join } from "path";

// ============================================================================
// Types
// ============================================================================

export interface MarketProject {
  id: string;
  tags: string | null;
  user_id: string;
  created_at: string;
  project_name: string;
  agent_contact: string | null;
  public_summary: string | null;
}

export interface Interest {
  id: string;
  status: string;
  target: { id: string; user_id: string; project_name: string };
  message: string;
  created_at: string;
  from_user_id: string;
  agent_contact: string | null;
  target_project_id: string;
}

export interface Project {
  id: string;
  tags: string | null;
  user_id: string;
  created_at: string;
  project_name: string;
  agent_contact: string | null;
  public_summary: string | null;
}

export interface PolicyRow {
  id: string;
  is_active: boolean;
  created_at: string;
  project_id: string;
  updated_at: string;
  patrol_scope: string;
  project_mode: string;
  reply_policy: string;
  owner_user_id: string;
  interest_policy: string;
  handoff_triggers: string[];
  notification_mode: string;
  market_patrol_interval: string;
  message_patrol_interval: string;
  collaborator_preferences: {
    automation: Record<string, boolean>;
    constraints: string;
    priorityTags: string[];
    preferredWorkingStyle: string;
  };
}

export interface PluginState {
  version: 1;
  lastPollAt: string | null;
  interests: { knownIds: string[]; lastSeenAt: string | null };
  market: { knownIds: string[]; lastSeenAt: string | null };
  cachedPolicy: PolicyRow | null;
  lastPolicyFetchAt: string | null;
  health: { consecutiveFailures: number; lastError: string | null };
}

export type ChangeEvent =
  | { type: "new_interest"; interest: Interest; projectName: string }
  | { type: "market_match"; project: MarketProject; matchReason: string };

export interface NotificationMessage {
  title: string;
  body: string;
  urgency: "high" | "normal" | "low";
  metadata: Record<string, string>;
}

// ============================================================================
// Errors
// ============================================================================

export class ClawborateApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly statusCode?: number,
  ) {
    super(`${code}: ${message}`);
    this.name = "ClawborateApiError";
  }
}

export class ClawborateNetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ClawborateNetworkError";
  }
}

// ============================================================================
// Configuration
// ============================================================================

export const OFFICIAL_BASE_URL = "https://xjljjxogsxumcnjyetwy.supabase.co";
export const OFFICIAL_ANON_KEY = "sb_publishable_dlgv32Zav_IaU_l6LVYu0A_CIz-Ww_u";

export const MARKET_INTERVAL_MS: Record<string, number> = {
  "10m": 600_000,
  "30m": 1_800_000,
  "1h": 3_600_000,
};
export const MIN_POLL_INTERVAL_MS = 60_000;
export const DEFAULT_POLL_INTERVAL_MS = 1_800_000;

export interface PluginConfig {
  baseUrl: string;
  anonKey: string;
  pollIntervalMs: number | null;
  policyRefreshIntervalMs: number;
  channelOverrides: string[];
  notificationModeOverride: string | null;
  stateDir: string;
  maxConsecutiveFailures: number;
  requestTimeoutMs: number;
}

export const DEFAULT_CONFIG: PluginConfig = {
  baseUrl: OFFICIAL_BASE_URL,
  anonKey: OFFICIAL_ANON_KEY,
  pollIntervalMs: null,
  policyRefreshIntervalMs: 300_000,
  channelOverrides: [],
  notificationModeOverride: null,
  stateDir: "~/.clawborate",
  maxConsecutiveFailures: 5,
  requestTimeoutMs: 30_000,
};

export function resolveIntervalMs(policyInterval: string | undefined): number {
  if (policyInterval === undefined || policyInterval === "manual") {
    return DEFAULT_POLL_INTERVAL_MS;
  }
  const ms = MARKET_INTERVAL_MS[policyInterval];
  return ms !== undefined ? Math.max(ms, MIN_POLL_INTERVAL_MS) : DEFAULT_POLL_INTERVAL_MS;
}

export function resolveStateDir(config: Pick<PluginConfig, "stateDir">): string {
  const raw = config.stateDir;
  if (raw.startsWith("~/") || raw === "~") {
    return join(homedir(), raw.slice(2));
  }
  return raw;
}

export async function loadConfig(stateDir: string): Promise<PluginConfig> {
  const resolvedDir = resolveStateDir({ stateDir });
  const configPath = join(resolvedDir, "config.json");
  let userConfig: Partial<PluginConfig> = {};
  try {
    const raw = await readFile(configPath, "utf-8");
    userConfig = JSON.parse(raw) as Partial<PluginConfig>;
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== "ENOENT") throw err;
  }
  return { ...DEFAULT_CONFIG, ...userConfig };
}

export function createDefaultState(): PluginState {
  return {
    version: 1,
    lastPollAt: null,
    interests: { knownIds: [], lastSeenAt: null },
    market: { knownIds: [], lastSeenAt: null },
    cachedPolicy: null,
    lastPolicyFetchAt: null,
    health: { consecutiveFailures: 0, lastError: null },
  };
}

// ============================================================================
// RPC Gateway Client
// ============================================================================

const RPC_ACTION_ALIASES: Record<string, string[]> = {
  get_project: ["get_project", "get-project"],
  create_project: ["create_project", "create"],
  update_project: ["update_project", "update"],
  delete_project: ["delete_project"],
  list_my_projects: ["list_my_projects"],
  list_market: ["list_market"],
  get_policy: ["get_policy", "get-policy"],
  submit_interest: ["submit_interest"],
  accept_interest: ["accept_interest", "accept-interest"],
  decline_interest: ["decline_interest", "decline-interest"],
  list_incoming_interests: ["list_incoming_interests"],
  list_outgoing_interests: ["list_outgoing_interests"],
  start_conversation: ["start_conversation"],
  update_conversation: ["update_conversation"],
  list_conversations: ["list_conversations"],
  list_messages: ["list_messages"],
  send_message: ["send_message"],
};

export class ClawborateClient {
  private readonly agentKey: string;
  private readonly baseUrl: string;
  private readonly anonKey: string;
  private readonly timeoutMs: number;

  constructor(
    agentKey: string,
    baseUrl: string = OFFICIAL_BASE_URL,
    anonKey: string = OFFICIAL_ANON_KEY,
    timeoutMs: number = 30_000,
  ) {
    this.agentKey = agentKey;
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.anonKey = anonKey;
    this.timeoutMs = timeoutMs;
  }

  private get rpcUrl(): string {
    return `${this.baseUrl}/rest/v1/rpc/agent_gateway`;
  }

  private rpcHeaders(): Record<string, string> {
    return {
      apikey: this.anonKey,
      Authorization: `Bearer ${this.anonKey}`,
      "Content-Type": "application/json",
    };
  }

  private async postAgentApi(
    action: string,
    payload: Record<string, unknown> = {},
  ): Promise<unknown> {
    const candidates = RPC_ACTION_ALIASES[action] ?? [action];
    const attempted: string[] = [];
    let lastError: ClawborateApiError | null = null;

    for (const candidate of candidates) {
      attempted.push(candidate);
      const body = JSON.stringify({
        p_agent_key: this.agentKey,
        p_action: candidate,
        p_payload: payload,
      });

      let response: Response;
      try {
        response = await fetch(this.rpcUrl, {
          method: "POST",
          headers: this.rpcHeaders(),
          body,
          signal: AbortSignal.timeout(this.timeoutMs),
        });
      } catch (err) {
        throw new ClawborateNetworkError(err instanceof Error ? err.message : String(err));
      }

      if (!response.ok) {
        let message: string;
        try {
          const errBody = (await response.json()) as Record<string, unknown>;
          message = typeof errBody["message"] === "string" ? errBody["message"] : `HTTP ${response.status}`;
        } catch {
          message = `HTTP ${response.status}`;
        }
        throw new ClawborateApiError("rpc_http_error", message, response.status);
      }

      let data: unknown;
      try {
        data = await response.json();
      } catch {
        const text = await response.text().catch(() => "");
        data = { message: text };
      }

      if (data !== null && typeof data === "object" && !Array.isArray(data) && "error" in (data as Record<string, unknown>)) {
        const d = data as Record<string, unknown>;
        const code = String(d["error"]);
        const msg = typeof d["message"] === "string" ? d["message"] : "";
        lastError = new ClawborateApiError(code, msg, response.status);
        if (code === "unknown_action") continue;
        throw lastError;
      }

      if (data !== null && typeof data === "object" && !Array.isArray(data) && "data" in (data as Record<string, unknown>)) {
        return (data as Record<string, unknown>)["data"];
      }
      return data;
    }

    if (lastError !== null) throw lastError;
    throw new ClawborateApiError("rpc_failed", `RPC failed for action ${action}; attempted ${attempted.join(", ")}`);
  }

  async listMyProjects(limit: number = 20): Promise<Project[]> {
    const data = await this.postAgentApi("list_my_projects", { limit });
    return Array.isArray(data) ? (data as Project[]) : [];
  }

  async listMarket(limit: number = 20): Promise<MarketProject[]> {
    const data = await this.postAgentApi("list_market", { limit });
    return Array.isArray(data) ? (data as MarketProject[]) : [];
  }

  async listIncomingInterests(): Promise<Interest[]> {
    const data = await this.postAgentApi("list_incoming_interests");
    return Array.isArray(data) ? (data as Interest[]) : [];
  }

  async getPolicy(projectId?: string): Promise<PolicyRow | null> {
    const payload: Record<string, unknown> = projectId ? { project_id: projectId } : {};
    const data = await this.postAgentApi("get_policy", payload);
    if (data === null || data === undefined) return null;
    if (Array.isArray(data) && data.length === 0) return null;
    return (data as PolicyRow) ?? null;
  }
}

export function makeClient(
  agentKey: string,
  options?: Partial<{ baseUrl: string; anonKey: string; timeoutMs: number }>,
): ClawborateClient {
  return new ClawborateClient(agentKey, options?.baseUrl, options?.anonKey, options?.timeoutMs);
}
