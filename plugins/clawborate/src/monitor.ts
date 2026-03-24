/**
 * monitor.ts — State persistence, change detection, and notification formatting.
 *
 * Merged from: state-store.ts + diff-engine.ts + notification-formatter.ts
 */

import { readFile, writeFile, rename, mkdir } from "fs/promises";
import { join } from "path";
import type { PluginLogger } from "openclaw/plugin-sdk/plugin-entry";
import type {
  PluginState,
  Interest,
  MarketProject,
  PolicyRow,
  ChangeEvent,
  NotificationMessage,
} from "./client.js";
import { createDefaultState } from "./client.js";

// ============================================================================
// StateStore
// ============================================================================

export class StateStore {
  private readonly logger: Pick<PluginLogger, "warn">;

  constructor(
    private readonly stateDir: string,
    logger?: Pick<PluginLogger, "warn">,
  ) {
    this.logger = logger ?? console;
  }

  async load(): Promise<PluginState> {
    const statePath = join(this.stateDir, "state.json");
    let raw: string;
    try {
      raw = await readFile(statePath, "utf-8");
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === "ENOENT") return createDefaultState();
      throw err;
    }
    try {
      return JSON.parse(raw) as PluginState;
    } catch {
      this.logger.warn("[clawborate] state.json corrupted — resetting to default");
      return createDefaultState();
    }
  }

  async save(state: PluginState): Promise<void> {
    await mkdir(this.stateDir, { recursive: true });
    const statePath = join(this.stateDir, "state.json");
    const tmpPath = statePath + ".tmp";
    await writeFile(tmpPath, JSON.stringify(state, null, 2), "utf-8");
    await rename(tmpPath, statePath);
  }

  async loadSecrets(): Promise<{ agentKey: string } | null> {
    const secretsPath = join(this.stateDir, "secrets.json");
    try {
      const raw = await readFile(secretsPath, "utf-8");
      return JSON.parse(raw) as { agentKey: string };
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === "ENOENT") return null;
      throw err;
    }
  }

  async saveSecrets(secrets: { agentKey: string }): Promise<void> {
    await mkdir(this.stateDir, { recursive: true });
    const secretsPath = join(this.stateDir, "secrets.json");
    const tmpPath = secretsPath + ".tmp";
    await writeFile(tmpPath, JSON.stringify(secrets, null, 2), "utf-8");
    await rename(tmpPath, secretsPath);
  }
}

// ============================================================================
// Diff Engine
// ============================================================================

export const MAX_KNOWN_IDS = 5000;

export function normalizeTags(tags: string | null | undefined): string[] {
  if (tags == null || tags === "") return [];
  const seen = new Set<string>();
  const result: string[] = [];
  for (const raw of tags.split(/[,;\n]+/)) {
    const trimmed = raw.trim().toLowerCase();
    if (trimmed.length > 0 && !seen.has(trimmed)) {
      seen.add(trimmed);
      result.push(trimmed);
    }
  }
  return result;
}

export function computeTagOverlap(
  projectTags: string | null | undefined,
  priorityTags: string[],
): string[] {
  const normalized = normalizeTags(projectTags);
  const prioritySet = new Set(priorityTags.map((t) => t.trim().toLowerCase()));
  return normalized.filter((t) => prioritySet.has(t));
}

export function computeDiff(
  prevState: PluginState,
  currentInterests: Interest[],
  currentMarket: MarketProject[],
  policy: PolicyRow | null,
): ChangeEvent[] {
  const events: ChangeEvent[] = [];

  const knownInterestIds = new Set(prevState.interests.knownIds);
  for (const interest of currentInterests) {
    if (!knownInterestIds.has(interest.id)) {
      events.push({ type: "new_interest", interest, projectName: interest.target.project_name });
    }
  }

  const knownMarketIds = new Set(prevState.market.knownIds);
  const priorityTags: string[] = policy?.collaborator_preferences?.priorityTags ?? [];
  const hasPriorityTags = priorityTags.length > 0;

  for (const project of currentMarket) {
    if (knownMarketIds.has(project.id)) continue;
    if (hasPriorityTags) {
      const overlap = computeTagOverlap(project.tags, priorityTags);
      if (overlap.length > 0) {
        events.push({ type: "market_match", project, matchReason: `tags: ${overlap.join(", ")}` });
      }
    } else {
      events.push({ type: "market_match", project, matchReason: "new_on_market" });
    }
  }

  return events;
}

export function updateState(
  prevState: PluginState,
  currentInterests: Interest[],
  currentMarket: MarketProject[],
): PluginState {
  const now = new Date().toISOString();
  return {
    ...prevState,
    lastPollAt: now,
    interests: {
      knownIds: mergePrunedIds(prevState.interests.knownIds, currentInterests.map((i) => i.id)),
      lastSeenAt: now,
    },
    market: {
      knownIds: mergePrunedIds(prevState.market.knownIds, currentMarket.map((p) => p.id)),
      lastSeenAt: now,
    },
  };
}

function mergePrunedIds(existing: string[], incoming: string[]): string[] {
  const existingSet = new Set(existing);
  const toAdd = incoming.filter((id) => !existingSet.has(id));
  const merged = [...existing, ...toAdd];
  return merged.length > MAX_KNOWN_IDS ? merged.slice(merged.length - MAX_KNOWN_IDS) : merged;
}

// ============================================================================
// Notification Formatter
// ============================================================================

const DASHBOARD_URL = "https://sunday-openclaw.github.io/clawborate/dashboard.html";

function truncate(text: string, maxLen: number): string {
  return text.length <= maxLen ? text : text.slice(0, maxLen) + "...";
}

export function formatNotifications(
  events: ChangeEvent[],
  notificationMode: string,
): NotificationMessage[] {
  if (events.length === 0) return [];

  const interestEvents = events.filter(
    (e): e is Extract<ChangeEvent, { type: "new_interest" }> => e.type === "new_interest",
  );
  let marketEvents = events.filter(
    (e): e is Extract<ChangeEvent, { type: "market_match" }> => e.type === "market_match",
  );

  if (notificationMode === "important_only") {
    marketEvents = marketEvents.filter((e) => e.matchReason.startsWith("tags:"));
  }

  const isVerbose = notificationMode === "verbose";
  const results: NotificationMessage[] = [];

  if (interestEvents.length > 0) {
    const count = interestEvents.length;
    const title = count === 1 ? "1 条新合作意向" : `${count} 条新合作意向`;
    const lines = interestEvents.map((e) => {
      const contact = e.interest.agent_contact ?? "(unknown)";
      const msg = isVerbose ? e.interest.message : truncate(e.interest.message, 80);
      return `• "${e.projectName}" <- ${contact}:\n  "${msg}"`;
    });
    results.push({
      title,
      body: lines.join("\n\n") + `\n\n查看详情: ${DASHBOARD_URL}`,
      urgency: "high",
      metadata: { eventType: "new_interest", count: String(count), dashboardUrl: DASHBOARD_URL },
    });
  }

  if (marketEvents.length > 0) {
    const count = marketEvents.length;
    const title = count === 1 ? "1 个新项目匹配你的标准" : `${count} 个新项目匹配你的标准`;
    const lines = marketEvents.map((e) => {
      const tags = e.project.tags ?? "";
      const summary = isVerbose ? (e.project.public_summary ?? "") : truncate(e.project.public_summary ?? "", 80);
      return `• "${e.project.project_name}" [${tags}]\n  "${summary}"`;
    });
    results.push({
      title,
      body: lines.join("\n\n") + `\n\n查看详情: ${DASHBOARD_URL}`,
      urgency: "normal",
      metadata: { eventType: "market_match", count: String(count), dashboardUrl: DASHBOARD_URL },
    });
  }

  return results;
}
