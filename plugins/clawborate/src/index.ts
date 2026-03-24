/**
 * index.ts — OpenClaw plugin entry point.
 *
 * Registers a background service that polls Clawborate for new interests
 * and market matches, then sends notifications through OpenClaw channels.
 */

import { join, dirname, resolve } from "path";
import { pathToFileURL } from "url";
import { createRequire } from "module";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import type {
  OpenClawPluginApi,
  OpenClawPluginServiceContext,
  PluginLogger,
} from "openclaw/plugin-sdk/plugin-entry";
import {
  ClawborateClient,
  makeClient,
  ClawborateApiError,
  resolveIntervalMs,
  loadConfig,
  DEFAULT_CONFIG,
  resolveStateDir,
} from "./client.js";
import type { PluginConfig, PolicyRow, NotificationMessage } from "./client.js";
import { StateStore, computeDiff, updateState, formatNotifications } from "./monitor.js";

// ============================================================================
// Channel sending — adapts to OpenClaw's runtime channel API
// ============================================================================

export interface ChannelSender {
  send(message: NotificationMessage): Promise<void>;
}

/**
 * Sends notifications through OpenClaw's platform-specific channel APIs.
 *
 * Channel overrides use the format "platform:destination", e.g.:
 * - "telegram:12345" → api.runtime.channel.telegram.sendMessageTelegram
 * - "discord:67890" → api.runtime.channel.discord.sendMessageDiscord
 */
class OpenClawChannelSender implements ChannelSender {
  private config: OpenClawPluginServiceContext["config"] | null = null;

  constructor(
    private readonly api: OpenClawPluginApi,
    private readonly channelOverrides: string[],
    private readonly logger: PluginLogger,
  ) {}

  /** Set the OpenClaw config (needed for feishu channel which uses cfg-based API) */
  setConfig(config: OpenClawPluginServiceContext["config"]): void {
    this.config = config;
  }

  async send(message: NotificationMessage): Promise<void> {
    const text = `**${message.title}**\n\n${message.body}`;
    if (this.channelOverrides.length === 0) {
      this.logger.warn("No channelOverrides configured — notification not delivered");
      return;
    }
    for (const target of this.channelOverrides) {
      const colonIdx = target.indexOf(":");
      if (colonIdx === -1) {
        this.logger.warn(`Invalid channel target format "${target}" — expected "platform:destination"`);
        continue;
      }
      const platform = target.slice(0, colonIdx);
      const destination = target.slice(colonIdx + 1);
      try {
        await this.sendToPlatform(platform, destination, text);
      } catch (err) {
        this.logger.warn(`Failed to send to ${target}: ${err instanceof Error ? err.message : String(err)}`);
      }
    }
  }

  private async sendToPlatform(platform: string, destination: string, text: string): Promise<void> {
    const runtime = this.api.runtime;
    switch (platform) {
      case "feishu": {
        if (!this.config) {
          this.logger.warn("Cannot send to feishu: OpenClaw config not available");
          break;
        }
        // Feishu send is not in runtime.channel — it's in the feishu extension bundle.
        // Resolve absolute path via a known export to bypass package.json exports map.
        const _require = createRequire(import.meta.url ?? __filename);
        const entryPath = _require.resolve("openclaw/plugin-sdk/plugin-entry");
        const feishuAbsPath = resolve(dirname(entryPath), "..", "extensions", "feishu", "index.js");
        const feishuMod = await import(pathToFileURL(feishuAbsPath).href) as {
          sendMessageFeishu: (params: { cfg: unknown; to: string; text: string }) => Promise<unknown>;
        };
        await feishuMod.sendMessageFeishu({ cfg: this.config, to: destination, text });
        break;
      }
      case "telegram":
        await runtime.channel.telegram.sendMessageTelegram(destination, text);
        break;
      case "discord":
        await runtime.channel.discord.sendMessageDiscord(destination, text);
        break;
      case "slack":
        await runtime.channel.slack.sendMessageSlack(destination, text);
        break;
      case "signal":
        await runtime.channel.signal.sendMessageSignal(destination, text);
        break;
      default:
        this.logger.warn(`Unsupported channel platform: ${platform}`);
    }
  }
}

/** In-memory sender for testing. */
export class InMemoryChannelSender implements ChannelSender {
  public readonly sent: NotificationMessage[] = [];
  async send(message: NotificationMessage): Promise<void> {
    this.sent.push(message);
  }
}

// ============================================================================
// Poller — periodic polling loop
// ============================================================================

export interface PollerDeps {
  client: ClawborateClient;
  store: StateStore;
  channel: ChannelSender;
  config: PluginConfig;
}

export class Poller {
  private timer: ReturnType<typeof setInterval> | null = null;
  private currentIntervalMs: number = 0;
  private cachedPolicy: PolicyRow | null = null;
  private lastPolicyFetchAt: number = 0;

  constructor(private readonly deps: PollerDeps) {}

  start(): void {
    if (this.timer) return;
    const intervalMs = this.deps.config.pollIntervalMs ?? resolveIntervalMs(undefined);
    this.currentIntervalMs = intervalMs;
    this.pollOnce().catch(() => {});
    this.timer = setInterval(() => { this.pollOnce().catch(() => {}); }, intervalMs);
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  get running(): boolean {
    return this.timer !== null;
  }

  async pollOnce(): Promise<void> {
    const { client, store, channel, config } = this.deps;
    let state = await store.load();

    try {
      await this.refreshPolicy(client, config);
      if (this.cachedPolicy && !this.cachedPolicy.is_active) return;

      const [interests, market] = await Promise.all([
        client.listIncomingInterests(),
        client.listMarket(50),
      ]);

      const isFirstRun =
        state.interests.knownIds.length === 0 &&
        state.market.knownIds.length === 0 &&
        state.lastPollAt === null;

      if (!isFirstRun) {
        const events = computeDiff(state, interests, market, this.cachedPolicy);
        if (events.length > 0) {
          const notificationMode =
            config.notificationModeOverride ?? this.cachedPolicy?.notification_mode ?? "important_only";
          const messages = formatNotifications(events, notificationMode);
          for (const msg of messages) {
            await channel.send(msg);
          }
        }
      }

      state = updateState(state, interests, market);
      state.cachedPolicy = this.cachedPolicy;
      state.lastPolicyFetchAt = new Date(this.lastPolicyFetchAt).toISOString();
      state.health.consecutiveFailures = 0;
      state.health.lastError = null;
      await store.save(state);
      this.maybeAdjustInterval();
    } catch (err) {
      state.health.consecutiveFailures++;
      state.health.lastError = err instanceof Error ? err.message : String(err);
      await store.save(state).catch(() => {});

      if (err instanceof ClawborateApiError && err.code === "invalid_agent_key") {
        this.stop();
        return;
      }
      if (state.health.consecutiveFailures >= config.maxConsecutiveFailures) {
        this.stop();
      }
    }
  }

  private async refreshPolicy(client: ClawborateClient, config: PluginConfig): Promise<void> {
    const now = Date.now();
    if (this.cachedPolicy !== null && now - this.lastPolicyFetchAt < config.policyRefreshIntervalMs) return;
    this.cachedPolicy = await client.getPolicy();
    this.lastPolicyFetchAt = now;
  }

  private maybeAdjustInterval(): void {
    if (!this.timer || this.deps.config.pollIntervalMs !== null) return;
    const newInterval = resolveIntervalMs(this.cachedPolicy?.market_patrol_interval);
    if (newInterval !== this.currentIntervalMs) {
      this.stop();
      this.currentIntervalMs = newInterval;
      this.timer = setInterval(() => { this.pollOnce().catch(() => {}); }, newInterval);
    }
  }
}

// ============================================================================
// OpenClaw Plugin Entry Point
// ============================================================================

let poller: Poller | null = null;

export default definePluginEntry({
  id: "clawborate",
  name: "Clawborate",
  description: "Polls Clawborate for new interests and market matches, sends notifications via channels",

  register(api: OpenClawPluginApi) {
    const logger = api.logger;

    api.registerService({
      id: "clawborate-monitor",

      async start(ctx: OpenClawPluginServiceContext) {
        const pluginConfig = api.pluginConfig ?? {};
        const agentKey = pluginConfig.agentKey as string | undefined;

        if (!agentKey) {
          // Try loading from local secrets as fallback
          const config = await loadConfig(DEFAULT_CONFIG.stateDir);
          const baseStateDir = ctx.stateDir || resolveStateDir(config);
          const stateDir = ctx.stateDir ? join(baseStateDir, "clawborate") : baseStateDir;
          const store = new StateStore(stateDir, ctx.logger);
          const secrets = await store.loadSecrets();
          if (!secrets?.agentKey) {
            logger.error("No agentKey in plugin config or secrets.json");
            return;
          }
          await startPoller(api, config, secrets.agentKey, ctx);
          return;
        }

        const config = await loadConfig(DEFAULT_CONFIG.stateDir);
        await startPoller(api, config, agentKey, ctx);
      },

      stop() {
        if (poller) {
          poller.stop();
          poller = null;
        }
      },
    });
  },
});

async function startPoller(
  api: OpenClawPluginApi,
  config: PluginConfig,
  agentKey: string,
  ctx: OpenClawPluginServiceContext,
): Promise<void> {
  const client = makeClient(agentKey, {
    baseUrl: config.baseUrl,
    anonKey: config.anonKey,
    timeoutMs: config.requestTimeoutMs,
  });

  // Validate key
  await client.listMyProjects(1);

  // Use plugin-specific subdirectory under OpenClaw's stateDir to avoid conflicts
  const baseStateDir = ctx.stateDir || resolveStateDir(config);
  const stateDir = ctx.stateDir ? join(baseStateDir, "clawborate") : baseStateDir;
  const store = new StateStore(stateDir, ctx.logger);

  const pluginConfig = api.pluginConfig ?? {};
  const channelOverrides: string[] = (pluginConfig.channelOverrides as string[]) ?? config.channelOverrides;
  const channel = new OpenClawChannelSender(api, channelOverrides, ctx.logger);
  channel.setConfig(ctx.config);

  poller = new Poller({ client, store, channel, config });
  poller.start();
  ctx.logger.info("Monitor started");
}

// Re-exports for library consumers
export { ClawborateClient, makeClient } from "./client.js";
export { StateStore, computeDiff, updateState, normalizeTags, formatNotifications } from "./monitor.js";
export type { PluginConfig, PluginState, ChangeEvent, NotificationMessage } from "./client.js";
