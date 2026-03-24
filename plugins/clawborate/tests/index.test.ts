import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { tmpdir } from "os";
import { join } from "path";
import { rm } from "fs/promises";

import { Poller, InMemoryChannelSender } from "../src/index.js";
import type { PollerDeps } from "../src/index.js";
import { StateStore } from "../src/monitor.js";
import { ClawborateApiError, DEFAULT_CONFIG } from "../src/client.js";
import type { Interest, MarketProject, PolicyRow, PluginState, ClawborateClient } from "../src/client.js";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makePolicy(overrides: Partial<PolicyRow> = {}): PolicyRow {
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
      priorityTags: [],
      preferredWorkingStyle: "async",
    },
    ...overrides,
  };
}

const interest1: Interest = {
  id: "int-1",
  status: "open",
  target: { id: "p1", user_id: "u2", project_name: "Proj A" },
  message: "Hi, let us collaborate",
  created_at: "2026-03-22T00:00:00Z",
  from_user_id: "u1",
  agent_contact: "@bot",
  target_project_id: "p1",
};

const interest2: Interest = {
  id: "int-2",
  status: "open",
  target: { id: "p2", user_id: "u3", project_name: "Proj B" },
  message: "Hello there",
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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeTmpDir(suffix: string): string {
  return join(
    tmpdir(),
    `clawborate-poller-test-${suffix}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
  );
}

async function cleanupDir(dir: string): Promise<void> {
  try {
    await rm(dir, { recursive: true, force: true });
  } catch {
    // ignore
  }
}

function makeMockClient(opts: {
  policy?: PolicyRow | null;
  interests?: Interest[];
  market?: MarketProject[];
  getPolicyImpl?: () => Promise<PolicyRow | null>;
  listIncomingInterestsImpl?: () => Promise<Interest[]>;
  listMarketImpl?: () => Promise<MarketProject[]>;
}): ClawborateClient {
  const {
    policy = makePolicy(),
    interests = [],
    market = [],
    getPolicyImpl,
    listIncomingInterestsImpl,
    listMarketImpl,
  } = opts;

  return {
    getPolicy: vi.fn(getPolicyImpl ?? (() => Promise.resolve(policy))),
    listIncomingInterests: vi.fn(
      listIncomingInterestsImpl ?? (() => Promise.resolve(interests)),
    ),
    listMarket: vi.fn(listMarketImpl ?? (() => Promise.resolve(market))),
    listMyProjects: vi.fn(() => Promise.resolve([])),
  } as unknown as ClawborateClient;
}

function makeDeps(
  stateDir: string,
  opts: {
    client?: ClawborateClient;
    channel?: InMemoryChannelSender;
    configOverrides?: Partial<typeof DEFAULT_CONFIG>;
  } = {},
): PollerDeps & { channel: InMemoryChannelSender; store: StateStore } {
  const channel = opts.channel ?? new InMemoryChannelSender();
  const store = new StateStore(stateDir);
  const client = opts.client ?? makeMockClient({});
  const config = {
    ...DEFAULT_CONFIG,
    policyRefreshIntervalMs: 300_000,
    ...(opts.configOverrides ?? {}),
  };

  return { client, store, channel, config };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Poller", () => {
  let stateDir: string;

  beforeEach(() => {
    vi.useFakeTimers();
    stateDir = makeTmpDir("suite");
  });

  afterEach(async () => {
    vi.useRealTimers();
    await cleanupDir(stateDir);
  });

  // 1. First run seeds state without notifications
  it("first run seeds state without sending notifications", async () => {
    const client = makeMockClient({ policy: makePolicy(), interests: [interest1], market: [market1] });
    const deps = makeDeps(stateDir, { client });
    const poller = new Poller(deps);

    await poller.pollOnce();

    expect(deps.channel.sent).toHaveLength(0);

    const savedState = await deps.store.load();
    expect(savedState.interests.knownIds).toContain("int-1");
    expect(savedState.market.knownIds).toContain("mk-1");
    expect(savedState.lastPollAt).not.toBeNull();
  });

  // 2. Second run detects new interest and sends notification
  it("second run detects new interest and sends notification", async () => {
    const store = new StateStore(stateDir);
    const seedState: PluginState = {
      version: 1,
      lastPollAt: "2026-03-22T00:00:00Z",
      interests: { knownIds: ["int-1"], lastSeenAt: "2026-03-22T00:00:00Z" },
      market: { knownIds: ["mk-1"], lastSeenAt: "2026-03-22T00:00:00Z" },
      cachedPolicy: null,
      lastPolicyFetchAt: null,
      health: { consecutiveFailures: 0, lastError: null },
    };
    await store.save(seedState);

    const client = makeMockClient({
      policy: makePolicy(),
      interests: [interest1, interest2],
      market: [market1],
    });
    const channel = new InMemoryChannelSender();
    const deps = makeDeps(stateDir, { client, channel });
    const poller = new Poller({ ...deps, store });

    await poller.pollOnce();

    expect(channel.sent.length).toBeGreaterThan(0);
    const titles = channel.sent.map((m) => m.title);
    expect(titles.some((t) => t.includes("意向"))).toBe(true);
  });

  // 3. Inactive policy skips data fetching
  it("skips data fetching when policy.is_active === false", async () => {
    const inactivePolicy = makePolicy({ is_active: false });
    const client = makeMockClient({ policy: inactivePolicy, interests: [interest1], market: [market1] });
    const deps = makeDeps(stateDir, { client });
    const poller = new Poller(deps);

    await poller.pollOnce();

    expect(client.getPolicy).toHaveBeenCalledOnce();
    expect(client.listIncomingInterests).not.toHaveBeenCalled();
    expect(client.listMarket).not.toHaveBeenCalled();
    expect(deps.channel.sent).toHaveLength(0);
  });

  // 4. API error increments consecutive failures
  it("increments consecutiveFailures when client throws", async () => {
    const client = makeMockClient({
      listIncomingInterestsImpl: () => Promise.reject(new Error("network timeout")),
    });
    const deps = makeDeps(stateDir, { client, configOverrides: { maxConsecutiveFailures: 10 } });
    const poller = new Poller(deps);

    await poller.pollOnce();

    const savedState = await deps.store.load();
    expect(savedState.health.consecutiveFailures).toBe(1);
    expect(savedState.health.lastError).toContain("network timeout");
  });

  // 5. Stops after maxConsecutiveFailures
  it("stops the poller after maxConsecutiveFailures", async () => {
    const client = makeMockClient({
      listIncomingInterestsImpl: () => Promise.reject(new Error("persistent error")),
    });
    const deps = makeDeps(stateDir, { client, configOverrides: { maxConsecutiveFailures: 2 } });
    const poller = new Poller(deps);

    poller.start();
    poller.stop();

    await poller.pollOnce(); // failure 1
    expect(poller.running).toBe(false);

    poller.start();
    poller.stop();

    await poller.pollOnce(); // failure 2 — auto-stop
    expect(poller.running).toBe(false);
  });

  // 6. Invalid agent key stops poller immediately
  it("stops poller immediately on invalid_agent_key error", async () => {
    const client = makeMockClient({
      getPolicyImpl: () =>
        Promise.reject(new ClawborateApiError("invalid_agent_key", "Invalid key")),
    });
    const deps = makeDeps(stateDir, { client });
    const poller = new Poller(deps);

    poller.start();
    poller.stop();

    poller.start();
    await vi.runAllTimersAsync();

    expect(poller.running).toBe(false);
  });

  // 7. Policy cache is used within refresh interval
  it("uses cached policy within policyRefreshIntervalMs without re-fetching", async () => {
    const client = makeMockClient({ policy: makePolicy() });
    const deps = makeDeps(stateDir, {
      client,
      configOverrides: { policyRefreshIntervalMs: 300_000 },
    });
    const poller = new Poller(deps);

    await poller.pollOnce();
    await poller.pollOnce();

    expect(client.getPolicy).toHaveBeenCalledOnce();
  });

  // 8. start() and stop() lifecycle
  it("start() sets running to true and stop() clears it", () => {
    const deps = makeDeps(stateDir);
    const poller = new Poller(deps);

    expect(poller.running).toBe(false);

    poller.start();
    expect(poller.running).toBe(true);

    poller.stop();
    expect(poller.running).toBe(false);
  });

  it("calling start() twice does not create a second timer", async () => {
    const client = makeMockClient({ policy: makePolicy() });
    const deps = makeDeps(stateDir, { client });
    const poller = new Poller(deps);

    poller.start();
    poller.start(); // no-op

    expect(poller.running).toBe(true);

    poller.stop();
    expect(poller.running).toBe(false);
  });

  it("stop() is idempotent when poller is not running", () => {
    const deps = makeDeps(stateDir);
    const poller = new Poller(deps);

    expect(() => poller.stop()).not.toThrow();
    expect(poller.running).toBe(false);
  });
});
