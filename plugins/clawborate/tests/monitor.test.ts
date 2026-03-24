import { describe, it, expect, vi } from "vitest";
import { tmpdir } from "os";
import { join } from "path";
import { mkdir, writeFile, rm, readFile } from "fs/promises";

import {
  StateStore,
  computeDiff,
  updateState,
  normalizeTags,
  computeTagOverlap,
  MAX_KNOWN_IDS,
  formatNotifications,
} from "../src/monitor.js";
import { createDefaultState } from "../src/client.js";
import type {
  Interest,
  MarketProject,
  PluginState,
  PolicyRow,
  ChangeEvent,
} from "../src/client.js";

// ============================================================================
// Shared Fixtures
// ============================================================================

const interest1: Interest = {
  id: "int-1",
  status: "open",
  target: { id: "p1", user_id: "u2", project_name: "Proj A" },
  message: "Hi",
  created_at: "2026-03-22T00:00:00Z",
  from_user_id: "u1",
  agent_contact: "@bot",
  target_project_id: "p1",
};

const interest2: Interest = {
  id: "int-2",
  status: "open",
  target: { id: "p2", user_id: "u3", project_name: "Proj B" },
  message: "Hello",
  created_at: "2026-03-23T00:00:00Z",
  from_user_id: "u4",
  agent_contact: null,
  target_project_id: "p2",
};

const market1: MarketProject = {
  id: "mk-1",
  tags: "ai, search",
  user_id: "u3",
  created_at: "2026-03-22T00:00:00Z",
  project_name: "Search Engine",
  agent_contact: null,
  public_summary: "A search project",
};

const market2: MarketProject = {
  id: "mk-2",
  tags: "blockchain, web3",
  user_id: "u5",
  created_at: "2026-03-23T00:00:00Z",
  project_name: "DeFi App",
  agent_contact: null,
  public_summary: "A defi project",
};

const emptyState: PluginState = {
  version: 1,
  lastPollAt: null,
  interests: { knownIds: [], lastSeenAt: null },
  market: { knownIds: [], lastSeenAt: null },
  cachedPolicy: null,
  lastPolicyFetchAt: null,
  health: { consecutiveFailures: 0, lastError: null },
};

function makePolicy(priorityTags: string[]): PolicyRow {
  return {
    id: "pol-1",
    is_active: true,
    created_at: "2026-03-01T00:00:00Z",
    project_id: "p1",
    updated_at: "2026-03-01T00:00:00Z",
    patrol_scope: "both",
    project_mode: "startup",
    reply_policy: "auto",
    owner_user_id: "u1",
    interest_policy: "auto",
    handoff_triggers: [],
    notification_mode: "moderate",
    market_patrol_interval: "30m",
    message_patrol_interval: "30m",
    collaborator_preferences: {
      automation: {},
      constraints: "",
      priorityTags,
      preferredWorkingStyle: "async",
    },
  };
}

// ============================================================================
// Helpers
// ============================================================================

function makeTmpDir(suffix: string): string {
  return join(
    tmpdir(),
    `clawborate-test-${suffix}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
  );
}

async function cleanupDir(dir: string): Promise<void> {
  try {
    await rm(dir, { recursive: true, force: true });
  } catch {
    // ignore
  }
}

// ============================================================================
// StateStore Tests
// ============================================================================

describe("StateStore", () => {
  describe("load()", () => {
    it("returns default state when state.json does not exist", async () => {
      const stateDir = makeTmpDir("load-missing");
      const store = new StateStore(stateDir);

      const state = await store.load();
      expect(state).toEqual(createDefaultState());

      await cleanupDir(stateDir);
    });

    it("round-trips state correctly after save and load", async () => {
      const stateDir = makeTmpDir("roundtrip");
      await mkdir(stateDir, { recursive: true });
      const store = new StateStore(stateDir);

      const custom: PluginState = {
        ...createDefaultState(),
        lastPollAt: "2026-03-24T12:00:00Z",
        interests: {
          knownIds: ["int-1", "int-2"],
          lastSeenAt: "2026-03-24T11:00:00Z",
        },
        health: {
          consecutiveFailures: 3,
          lastError: "timeout",
        },
      };

      await store.save(custom);
      const loaded = await store.load();
      expect(loaded).toEqual(custom);

      await cleanupDir(stateDir);
    });

    it("returns default state when state.json contains corrupted JSON", async () => {
      const stateDir = makeTmpDir("corrupt");
      await mkdir(stateDir, { recursive: true });
      await writeFile(join(stateDir, "state.json"), "{ not valid json }", "utf-8");

      const store = new StateStore(stateDir);
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);

      const state = await store.load();
      expect(state).toEqual(createDefaultState());
      expect(warnSpy).toHaveBeenCalledOnce();

      warnSpy.mockRestore();
      await cleanupDir(stateDir);
    });
  });

  describe("save()", () => {
    it("creates stateDir recursively if it doesn't exist", async () => {
      const stateDir = makeTmpDir("autocreate");
      const store = new StateStore(stateDir);

      await expect(store.save(createDefaultState())).resolves.toBeUndefined();

      const raw = await readFile(join(stateDir, "state.json"), "utf-8");
      expect(JSON.parse(raw)).toEqual(createDefaultState());

      await cleanupDir(stateDir);
    });

    it("performs atomic write via .tmp file and rename", async () => {
      const stateDir = makeTmpDir("atomic");
      const store = new StateStore(stateDir);

      await store.save(createDefaultState());

      const statePath = join(stateDir, "state.json");
      const tmpPath = statePath + ".tmp";

      const finalRaw = await readFile(statePath, "utf-8");
      expect(JSON.parse(finalRaw)).toEqual(createDefaultState());

      let tmpExists = false;
      try {
        await readFile(tmpPath, "utf-8");
        tmpExists = true;
      } catch (err) {
        if ((err as NodeJS.ErrnoException).code !== "ENOENT") {
          throw err;
        }
      }
      expect(tmpExists).toBe(false);

      await cleanupDir(stateDir);
    });
  });

  describe("loadSecrets()", () => {
    it("returns null when secrets.json does not exist", async () => {
      const stateDir = makeTmpDir("secrets-missing");
      const store = new StateStore(stateDir);

      const result = await store.loadSecrets();
      expect(result).toBeNull();

      await cleanupDir(stateDir);
    });

    it("round-trips secrets correctly after saveSecrets and loadSecrets", async () => {
      const stateDir = makeTmpDir("secrets-roundtrip");
      const store = new StateStore(stateDir);

      const secrets = { agentKey: "cm_sk_live_test_abc123" };
      await store.saveSecrets(secrets);
      const loaded = await store.loadSecrets();
      expect(loaded).toEqual(secrets);

      await cleanupDir(stateDir);
    });
  });
});

// ============================================================================
// Diff Engine Tests
// ============================================================================

describe("computeDiff", () => {
  it("emits all items as events on first run (empty knownIds)", () => {
    const events = computeDiff(emptyState, [interest1], [market1], null);

    expect(events).toHaveLength(2);
    expect(events[0]).toMatchObject({ type: "new_interest", interest: interest1, projectName: "Proj A" });
    expect(events[1]).toMatchObject({ type: "market_match", project: market1, matchReason: "new_on_market" });
  });

  it("emits nothing when all IDs are already in knownIds", () => {
    const state: PluginState = {
      ...emptyState,
      interests: { knownIds: ["int-1"], lastSeenAt: null },
      market: { knownIds: ["mk-1"], lastSeenAt: null },
    };

    const events = computeDiff(state, [interest1], [market1], null);
    expect(events).toHaveLength(0);
  });

  it("emits only new interests when some are already known", () => {
    const state: PluginState = {
      ...emptyState,
      interests: { knownIds: ["int-1"], lastSeenAt: null },
    };

    const events = computeDiff(state, [interest1, interest2], [], null);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ type: "new_interest", interest: interest2, projectName: "Proj B" });
  });

  it("emits market_match with matchReason listing overlapping tags when priorityTags match", () => {
    const policy = makePolicy(["ai"]);

    const events = computeDiff(emptyState, [], [market1], policy);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      type: "market_match",
      project: market1,
      matchReason: "tags: ai",
    });
  });

  it("does not emit market project when no tag overlap with priorityTags", () => {
    const policy = makePolicy(["ai"]);

    const events = computeDiff(emptyState, [], [market2], policy);
    expect(events).toHaveLength(0);
  });

  it("emits all new market projects with new_on_market when priorityTags is empty", () => {
    const policy = makePolicy([]);

    const events = computeDiff(emptyState, [], [market1, market2], policy);
    expect(events).toHaveLength(2);
    expect(events[0]).toMatchObject({ type: "market_match", matchReason: "new_on_market" });
    expect(events[1]).toMatchObject({ type: "market_match", matchReason: "new_on_market" });
  });
});

describe("updateState", () => {
  it("adds new IDs to knownIds", () => {
    const state: PluginState = {
      ...emptyState,
      interests: { knownIds: ["int-0"], lastSeenAt: null },
      market: { knownIds: ["mk-0"], lastSeenAt: null },
    };

    const next = updateState(state, [interest1], [market1]);

    expect(next.interests.knownIds).toContain("int-0");
    expect(next.interests.knownIds).toContain("int-1");
    expect(next.market.knownIds).toContain("mk-0");
    expect(next.market.knownIds).toContain("mk-1");
  });

  it("prunes knownIds to MAX_KNOWN_IDS using FIFO", () => {
    const existingIds = Array.from({ length: MAX_KNOWN_IDS }, (_, i) => `old-${i}`);
    const state: PluginState = {
      ...emptyState,
      interests: { knownIds: existingIds, lastSeenAt: null },
    };

    const newInterest: Interest = { ...interest1, id: "brand-new" };
    const next = updateState(state, [newInterest], []);

    expect(next.interests.knownIds).toHaveLength(MAX_KNOWN_IDS);
    expect(next.interests.knownIds[MAX_KNOWN_IDS - 1]).toBe("brand-new");
    expect(next.interests.knownIds).not.toContain("old-0");
    expect(next.interests.knownIds).toContain("old-1");
  });

  it("sets lastPollAt and lastSeenAt to current ISO timestamps", () => {
    const before = Date.now();
    const next = updateState(emptyState, [interest1], [market1]);
    const after = Date.now();

    expect(next.lastPollAt).not.toBeNull();
    expect(next.interests.lastSeenAt).not.toBeNull();
    expect(next.market.lastSeenAt).not.toBeNull();

    const pollTime = new Date(next.lastPollAt!).getTime();
    expect(pollTime).toBeGreaterThanOrEqual(before);
    expect(pollTime).toBeLessThanOrEqual(after);
  });

  it("does not mutate the input state", () => {
    const original = JSON.parse(JSON.stringify(emptyState)) as PluginState;
    updateState(emptyState, [interest1], [market1]);
    expect(emptyState).toEqual(original);
  });
});

describe("normalizeTags", () => {
  it('splits "ai, search , ML" into ["ai", "search", "ml"]', () => {
    expect(normalizeTags("ai, search , ML")).toEqual(["ai", "search", "ml"]);
  });

  it("returns [] for null", () => {
    expect(normalizeTags(null)).toEqual([]);
  });

  it("returns [] for undefined", () => {
    expect(normalizeTags(undefined)).toEqual([]);
  });

  it("returns [] for empty string", () => {
    expect(normalizeTags("")).toEqual([]);
  });

  it("splits by semicolons as well", () => {
    expect(normalizeTags("ai;search;ML")).toEqual(["ai", "search", "ml"]);
  });

  it("handles mixed comma and semicolon delimiters", () => {
    expect(normalizeTags("ai,search;ML")).toEqual(["ai", "search", "ml"]);
  });
});

describe("computeTagOverlap", () => {
  it('finds overlap between "ai, search" and ["ai"]', () => {
    expect(computeTagOverlap("ai, search", ["ai"])).toEqual(["ai"]);
  });

  it("returns [] when there is no overlap", () => {
    expect(computeTagOverlap("blockchain, web3", ["ai", "ml"])).toEqual([]);
  });

  it("returns [] when projectTags is null", () => {
    expect(computeTagOverlap(null, ["ai"])).toEqual([]);
  });

  it("is case-insensitive", () => {
    expect(computeTagOverlap("AI, Search", ["ai", "search"])).toEqual(["ai", "search"]);
  });

  it("returns all overlapping tags when multiple match", () => {
    const overlap = computeTagOverlap("ai, search, ml", ["ai", "ml", "blockchain"]);
    expect(overlap).toEqual(["ai", "ml"]);
  });
});

// ============================================================================
// Notification Formatter Tests
// ============================================================================

const DASHBOARD_URL = "https://sunday-openclaw.github.io/clawborate/dashboard.html";

const interestEvent: ChangeEvent = {
  type: "new_interest",
  interest: {
    id: "i1",
    status: "open",
    target: { id: "p1", user_id: "u2", project_name: "AI Research" },
    message:
      "I am very interested in collaborating on this fascinating project about artificial intelligence research",
    created_at: "2026-03-22T00:00:00Z",
    from_user_id: "u1",
    agent_contact: "@researcher-bot",
    target_project_id: "p1",
  },
  projectName: "AI Research",
};

const marketEvent: ChangeEvent = {
  type: "market_match",
  project: {
    id: "m1",
    tags: "ai, ml",
    user_id: "u3",
    created_at: "2026-03-22T00:00:00Z",
    project_name: "Neural Search",
    agent_contact: null,
    public_summary:
      "Building a neural search engine with transformer architectures for semantic understanding of documents and code",
  },
  matchReason: "tags: ai",
};

const marketEventNoTag: ChangeEvent = {
  type: "market_match",
  project: {
    id: "m2",
    tags: "biology",
    user_id: "u4",
    created_at: "2026-03-22T00:00:00Z",
    project_name: "Bio Toolkit",
    agent_contact: null,
    public_summary: "Open source toolkit",
  },
  matchReason: "new_on_market",
};

describe("formatNotifications", () => {
  it("returns empty array when events is empty", () => {
    const result = formatNotifications([], "moderate");
    expect(result).toEqual([]);
  });

  it("formats a single interest event correctly", () => {
    const result = formatNotifications([interestEvent], "moderate");
    expect(result).toHaveLength(1);
    const msg = result[0];
    expect(msg.title).toBe("1 条新合作意向");
    expect(msg.urgency).toBe("high");
    expect(msg.body).toContain('"AI Research"');
    expect(msg.body).toContain("@researcher-bot");
    expect(msg.body).toContain(DASHBOARD_URL);
  });

  it("batches multiple interests into a single message with count in title", () => {
    const secondInterest: ChangeEvent = {
      type: "new_interest",
      interest: {
        id: "i2",
        status: "open",
        target: { id: "p2", user_id: "u5", project_name: "ML Platform" },
        message: "Would love to collaborate",
        created_at: "2026-03-23T00:00:00Z",
        from_user_id: "u6",
        agent_contact: "@ml-bot",
        target_project_id: "p2",
      },
      projectName: "ML Platform",
    };

    const result = formatNotifications([interestEvent, secondInterest], "moderate");
    expect(result).toHaveLength(1);
    expect(result[0].title).toBe("2 条新合作意向");
    expect(result[0].body).toContain('"AI Research"');
    expect(result[0].body).toContain('"ML Platform"');
  });

  it("includes all market matches in moderate mode", () => {
    const result = formatNotifications([marketEvent, marketEventNoTag], "moderate");
    const marketMsg = result.find((m) => m.metadata.eventType === "market_match");
    expect(marketMsg).toBeDefined();
    expect(marketMsg!.body).toContain('"Neural Search"');
    expect(marketMsg!.body).toContain('"Bio Toolkit"');
    expect(marketMsg!.metadata.count).toBe("2");
  });

  it("includes only tag-matched market events in important_only mode", () => {
    const result = formatNotifications([marketEvent, marketEventNoTag], "important_only");
    const marketMsg = result.find((m) => m.metadata.eventType === "market_match");
    expect(marketMsg).toBeDefined();
    expect(marketMsg!.body).toContain('"Neural Search"');
    expect(marketMsg!.body).not.toContain('"Bio Toolkit"');
    expect(marketMsg!.metadata.count).toBe("1");
  });

  it("returns no market message in important_only mode when all events have new_on_market reason", () => {
    const result = formatNotifications([marketEventNoTag], "important_only");
    const marketMsg = result.find((m) => m.metadata.eventType === "market_match");
    expect(marketMsg).toBeUndefined();
    expect(result).toHaveLength(0);
  });

  it("does not truncate messages in verbose mode", () => {
    const result = formatNotifications([interestEvent], "verbose");
    expect(result).toHaveLength(1);
    expect(result[0].body).toContain(
      "I am very interested in collaborating on this fascinating project about artificial intelligence research",
    );
    expect(result[0].body).not.toContain("...");
  });

  it("truncates long messages at 80 chars with ... in non-verbose mode", () => {
    const result = formatNotifications([interestEvent], "moderate");
    expect(result).toHaveLength(1);
    const body = result[0].body;
    expect(body).toContain("I am very interested in collaborating on this fascinating project about artifici...");
  });

  it("returns 2 messages for mixed interest and market events", () => {
    const result = formatNotifications([interestEvent, marketEvent], "moderate");
    expect(result).toHaveLength(2);
    const interestMsg = result.find((m) => m.metadata.eventType === "new_interest");
    const marketMsg = result.find((m) => m.metadata.eventType === "market_match");
    expect(interestMsg).toBeDefined();
    expect(marketMsg).toBeDefined();
  });

  it("sets correct metadata fields on both message types", () => {
    const result = formatNotifications([interestEvent, marketEvent], "moderate");

    const interestMsg = result.find((m) => m.metadata.eventType === "new_interest");
    expect(interestMsg).toBeDefined();
    expect(interestMsg!.metadata.eventType).toBe("new_interest");
    expect(interestMsg!.metadata.count).toBe("1");
    expect(interestMsg!.metadata.dashboardUrl).toBe(DASHBOARD_URL);
    expect(interestMsg!.urgency).toBe("high");

    const marketMsg = result.find((m) => m.metadata.eventType === "market_match");
    expect(marketMsg).toBeDefined();
    expect(marketMsg!.metadata.eventType).toBe("market_match");
    expect(marketMsg!.metadata.count).toBe("1");
    expect(marketMsg!.metadata.dashboardUrl).toBe(DASHBOARD_URL);
    expect(marketMsg!.urgency).toBe("normal");
  });
});
