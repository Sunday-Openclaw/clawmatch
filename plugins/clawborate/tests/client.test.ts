import { describe, it, expect, vi, afterEach } from "vitest";
import {
  ClawborateClient,
  makeClient,
  ClawborateApiError,
  ClawborateNetworkError,
} from "../src/client.js";
import type { MarketProject, Interest } from "../src/client.js";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const MARKET_FIXTURE: MarketProject = {
  id: "abc-123",
  tags: "ai,search",
  user_id: "user-1",
  created_at: "2026-03-22T00:00:00Z",
  project_name: "Test Project",
  agent_contact: null,
  public_summary: "A test project",
};

const INTEREST_FIXTURE: Interest = {
  id: "int-1",
  status: "open",
  target: {
    id: "proj-1",
    user_id: "user-2",
    project_name: "Target Project",
  },
  message: "Hello",
  created_at: "2026-03-22T00:00:00Z",
  from_user_id: "user-1",
  agent_contact: "@bot",
  target_project_id: "proj-1",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TEST_BASE_URL = "https://test.supabase.co";
const TEST_ANON_KEY = "test-anon-key";
const TEST_AGENT_KEY = "cm_sk_live_test";

function makeTestClient(): ClawborateClient {
  return new ClawborateClient(TEST_AGENT_KEY, TEST_BASE_URL, TEST_ANON_KEY, 5_000);
}

/** Build a mock fetch that returns a gateway success response wrapping `data`. */
function mockFetchSuccess(data: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ data }),
    } as unknown as Response),
  );
}

/** Build a mock fetch that returns a raw (unwrapped) gateway success response. */
function mockFetchSuccessRaw(body: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => body,
    } as unknown as Response),
  );
}

/** Build a mock fetch that returns a gateway-level error JSON. */
function mockFetchGatewayError(code: string, message: string): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ error: code, message }),
    } as unknown as Response),
  );
}

/** Build a mock fetch that throws a network-level error. */
function mockFetchNetworkError(message: string): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new TypeError(message)),
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ClawborateClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // -------------------------------------------------------------------------
  // 1. Successful listMarket call
  // -------------------------------------------------------------------------

  describe("listMarket", () => {
    it("calls the correct URL, headers, and body; returns parsed data", async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ data: [MARKET_FIXTURE] }),
      } as unknown as Response);
      vi.stubGlobal("fetch", mockFetch);

      const client = makeTestClient();
      const result = await client.listMarket(10);

      // Verify it was called once
      expect(mockFetch).toHaveBeenCalledTimes(1);

      // Verify URL
      const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
      expect(url).toBe(`${TEST_BASE_URL}/rest/v1/rpc/agent_gateway`);

      // Verify headers
      const headers = init.headers as Record<string, string>;
      expect(headers["apikey"]).toBe(TEST_ANON_KEY);
      expect(headers["Authorization"]).toBe(`Bearer ${TEST_ANON_KEY}`);
      expect(headers["Content-Type"]).toBe("application/json");

      // Verify body shape
      const body = JSON.parse(init.body as string) as Record<string, unknown>;
      expect(body["p_agent_key"]).toBe(TEST_AGENT_KEY);
      expect(body["p_action"]).toBe("list_market");
      expect((body["p_payload"] as Record<string, unknown>)["limit"]).toBe(10);

      // Verify returned data
      expect(result).toHaveLength(1);
      expect(result[0]).toEqual(MARKET_FIXTURE);
    });

    it("returns an empty array when data is empty", async () => {
      mockFetchSuccess([]);
      const client = makeTestClient();
      const result = await client.listMarket();
      expect(result).toEqual([]);
    });
  });

  // -------------------------------------------------------------------------
  // 2. Successful listIncomingInterests call
  // -------------------------------------------------------------------------

  describe("listIncomingInterests", () => {
    it("sends correct action and returns interest list", async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ data: [INTEREST_FIXTURE] }),
      } as unknown as Response);
      vi.stubGlobal("fetch", mockFetch);

      const client = makeTestClient();
      const result = await client.listIncomingInterests();

      expect(mockFetch).toHaveBeenCalledTimes(1);
      const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
      const body = JSON.parse(init.body as string) as Record<string, unknown>;
      expect(body["p_action"]).toBe("list_incoming_interests");

      expect(result).toHaveLength(1);
      expect(result[0]).toEqual(INTEREST_FIXTURE);
      expect(result[0]!.target.project_name).toBe("Target Project");
    });

    it("returns an empty array when no interests exist", async () => {
      mockFetchSuccess([]);
      const client = makeTestClient();
      const result = await client.listIncomingInterests();
      expect(result).toEqual([]);
    });
  });

  // -------------------------------------------------------------------------
  // 3. API error response
  // -------------------------------------------------------------------------

  describe("API error handling", () => {
    it("throws ClawborateApiError on invalid_agent_key error", async () => {
      mockFetchGatewayError("invalid_agent_key", "The provided agent key is not valid");
      const client = makeTestClient();

      await expect(client.listMarket()).rejects.toThrow(ClawborateApiError);
      await expect(client.listMarket()).rejects.toMatchObject({
        code: "invalid_agent_key",
        message: "invalid_agent_key: The provided agent key is not valid",
      });
    });

    it("throws ClawborateApiError with the correct code and statusCode on HTTP error", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue({
          ok: false,
          status: 403,
          json: async () => ({ message: "Forbidden" }),
        } as unknown as Response),
      );

      const client = makeTestClient();
      await expect(client.listMarket()).rejects.toThrow(ClawborateApiError);
      await expect(client.listMarket()).rejects.toMatchObject({
        code: "rpc_http_error",
        statusCode: 403,
      });
    });
  });

  // -------------------------------------------------------------------------
  // 4. Network failure
  // -------------------------------------------------------------------------

  describe("Network failure", () => {
    it("throws ClawborateNetworkError when fetch throws", async () => {
      mockFetchNetworkError("Failed to connect");
      const client = makeTestClient();

      await expect(client.listMarket()).rejects.toThrow(ClawborateNetworkError);
      await expect(client.listMarket()).rejects.toThrow("Failed to connect");
    });

    it("wraps non-Error throws into ClawborateNetworkError", async () => {
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue("string error"));
      const client = makeTestClient();

      await expect(client.listMarket()).rejects.toThrow(ClawborateNetworkError);
    });
  });

  // -------------------------------------------------------------------------
  // 5. getPolicy
  // -------------------------------------------------------------------------

  describe("getPolicy", () => {
    it("returns null when API returns null", async () => {
      mockFetchSuccessRaw(null);
      const client = makeTestClient();
      const result = await client.getPolicy();
      expect(result).toBeNull();
    });

    it("returns null when API returns empty data envelope", async () => {
      mockFetchSuccess(null);
      const client = makeTestClient();
      const result = await client.getPolicy();
      expect(result).toBeNull();
    });

    it("returns null when API returns an empty array", async () => {
      mockFetchSuccess([]);
      const client = makeTestClient();
      const result = await client.getPolicy();
      expect(result).toBeNull();
    });

    it("passes project_id in payload when provided", async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ data: null }),
      } as unknown as Response);
      vi.stubGlobal("fetch", mockFetch);

      const client = makeTestClient();
      await client.getPolicy("proj-abc");

      const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
      const body = JSON.parse(init.body as string) as Record<string, unknown>;
      expect((body["p_payload"] as Record<string, unknown>)["project_id"]).toBe("proj-abc");
    });

    it("sends empty payload when no project_id provided", async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ data: null }),
      } as unknown as Response);
      vi.stubGlobal("fetch", mockFetch);

      const client = makeTestClient();
      await client.getPolicy();

      const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
      const body = JSON.parse(init.body as string) as Record<string, unknown>;
      expect(body["p_payload"]).toEqual({});
    });
  });

  // -------------------------------------------------------------------------
  // 6. Action alias retry
  // -------------------------------------------------------------------------

  describe("Action alias retry", () => {
    it("retries with the next alias when unknown_action is returned", async () => {
      const mockFetch = vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          json: async () => ({ error: "unknown_action", message: "Action not found" }),
        } as unknown as Response)
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          json: async () => ({ data: null }),
        } as unknown as Response);
      vi.stubGlobal("fetch", mockFetch);

      const client = makeTestClient();
      const result = await client.getPolicy();

      expect(mockFetch).toHaveBeenCalledTimes(2);

      const firstBody = JSON.parse(
        (mockFetch.mock.calls[0] as [string, RequestInit])[1].body as string,
      ) as Record<string, unknown>;
      expect(firstBody["p_action"]).toBe("get_policy");

      const secondBody = JSON.parse(
        (mockFetch.mock.calls[1] as [string, RequestInit])[1].body as string,
      ) as Record<string, unknown>;
      expect(secondBody["p_action"]).toBe("get-policy");

      expect(result).toBeNull();
    });

    it("throws ClawborateApiError after all aliases are exhausted on unknown_action", async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ error: "unknown_action", message: "Action not found" }),
      } as unknown as Response);
      vi.stubGlobal("fetch", mockFetch);

      const client = makeTestClient();
      await expect(client.getPolicy()).rejects.toThrow(ClawborateApiError);
      await expect(client.getPolicy()).rejects.toMatchObject({
        code: "unknown_action",
      });
    });

    it("does not retry on non-unknown_action errors", async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ error: "permission_denied", message: "Denied" }),
      } as unknown as Response);
      vi.stubGlobal("fetch", mockFetch);

      const client = makeTestClient();
      await expect(client.listMarket()).rejects.toMatchObject({
        code: "permission_denied",
      });
      expect(mockFetch).toHaveBeenCalledTimes(1);
    });
  });

  // -------------------------------------------------------------------------
  // makeClient factory
  // -------------------------------------------------------------------------

  describe("makeClient factory", () => {
    it("creates a ClawborateClient with the provided options", async () => {
      mockFetchSuccess([MARKET_FIXTURE]);
      const client = makeClient(TEST_AGENT_KEY, {
        baseUrl: TEST_BASE_URL,
        anonKey: TEST_ANON_KEY,
        timeoutMs: 5_000,
      });
      const result = await client.listMarket();
      expect(result[0]).toEqual(MARKET_FIXTURE);
    });
  });

  // -------------------------------------------------------------------------
  // listMyProjects
  // -------------------------------------------------------------------------

  describe("listMyProjects", () => {
    it("returns the project list from the API", async () => {
      const project = { ...MARKET_FIXTURE, id: "proj-my-1" };
      mockFetchSuccess([project]);
      const client = makeTestClient();
      const result = await client.listMyProjects(5);
      expect(result).toHaveLength(1);
      expect(result[0]!.id).toBe("proj-my-1");
    });
  });
});
