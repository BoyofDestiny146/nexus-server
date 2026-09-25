import assert from "node:assert/strict";
import { test } from "node:test";
import {
  deviceOnlineLabel,
  formatRevelDiscoverError,
  planRevelDiscover,
  revelDiscoverFailedBeforeApi,
  revelDiscoverPath,
  revelDiscoverRequestLine,
  revelDiscoverUrl,
  REVEL_DISCOVER_CLICK_RECEIVED,
  shouldClearSessionOn401,
} from "./revelDiscover.ts";

test("discover path uses the CareConnect route, not Revel directly", () => {
  const agentId = "agt_123";
  assert.equal(
    revelDiscoverPath(agentId),
    "/agent/agt_123/integrations/revel/discover",
  );
  assert.equal(
    revelDiscoverUrl(agentId),
    "/api/agent/agt_123/integrations/revel/discover",
  );
  assert.equal(revelDiscoverPath(agentId).includes("reveldigital.com"), false);
});

test("connected with no typed key → POST discover", () => {
  assert.deepEqual(
    planRevelDiscover({ agentId: "agt_1", connected: true, typedKey: "" }),
    { action: "discover" },
  );
});

test("typed unsaved key → save then discover (no silent no-op)", () => {
  assert.deepEqual(
    planRevelDiscover({
      agentId: "agt_1",
      connected: false,
      typedKey: "  developer-api-key  ",
    }),
    { action: "save-then-discover", apiKey: "developer-api-key" },
  );
});

test("no key and not connected → need-key, never silent", () => {
  assert.deepEqual(
    planRevelDiscover({ agentId: "agt_1", connected: false, typedKey: "" }),
    { action: "need-key" },
  );
});

test("busy and missing agent are explicit", () => {
  assert.equal(
    planRevelDiscover({ agentId: "agt_1", busy: true, connected: true }).action,
    "busy",
  );
  assert.equal(planRevelDiscover({ agentId: "", connected: true }).action, "missing-agent");
});

test("401 copy is safe and specific", () => {
  assert.equal(
    formatRevelDiscoverError({ code: 401, message: "anything" } as Error & { code: number }),
    "Revel authentication failed (401)",
  );
  const err = Object.assign(new Error("Revel authentication failed (401)"), { code: 401 });
  assert.equal(formatRevelDiscoverError(err), "Revel authentication failed (401)");
  const other = Object.assign(new Error("Revel integration is not connected"), { code: 404 });
  assert.equal(formatRevelDiscoverError(other), "Revel integration is not connected");
});

test("device online labels", () => {
  assert.equal(deviceOnlineLabel(true), "Online");
  assert.equal(deviceOnlineLabel(false), "Offline");
  assert.equal(deviceOnlineLabel(null), "Unknown");
});

test("visible/client diagnostics never include secrets", () => {
  assert.equal(REVEL_DISCOVER_CLICK_RECEIVED, "Discover click received");
  assert.equal(
    revelDiscoverRequestLine("agt_9"),
    "revel discover request route=/api/agent/agt_9/integrations/revel/discover",
  );
  assert.equal(
    revelDiscoverFailedBeforeApi("network down"),
    "Discover failed before API call: network down",
  );
  assert.equal(revelDiscoverRequestLine("agt_9").toLowerCase().includes("key"), false);
});

test("Revel envelope 401 must not clear the dashboard JWT", () => {
  assert.equal(
    shouldClearSessionOn401("/agent/agt/integrations/revel/discover", "Revel authentication failed (401)"),
    false,
  );
  assert.equal(shouldClearSessionOn401("/auth/login", "username or password is incorrect"), true);
});
