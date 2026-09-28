import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const integrations = readFileSync(
  join(here, "../components/ClientIntegrations.tsx"),
  "utf8",
);
const caddy = readFileSync(join(here, "../../deploy/Caddyfile.nexus"), "utf8");
const envExample = readFileSync(join(here, "../../deploy/.env.example"), "utf8");
const engine = readFileSync(
  join(here, "../../api/careconnect_api/assessment_engine/engine.py"),
  "utf8",
);

test("web DEFAULT_PORTAL uses canonical nexus.warehouse-13.biz", () => {
  assert.match(integrations, /const DEFAULT_PORTAL = "https:\/\/nexus\.warehouse-13\.biz"/);
  assert.doesNotMatch(integrations, /DEFAULT_PORTAL = "https:\/\/care\.nexus\.warehouse-13\.biz"/);
  assert.match(integrations, /\/api\/v1\/integrations\/careconnect\/assessment/);
});

test("Caddy keeps ws and ota hosts unchanged while portal moves to nexus", () => {
  assert.match(caddy, /^nexus\.warehouse-13\.biz \{/m);
  assert.match(caddy, /redir https:\/\/nexus\.warehouse-13\.biz\{uri\} 308/);
  assert.match(caddy, /handle \/api\/v1\/\*/);
  assert.match(caddy, /handle \/ws\/\*/);
  const ota = caddy.slice(caddy.indexOf("ota.nexus.warehouse-13.biz {"));
  const otaBlock = ota.slice(0, ota.indexOf("}\n") + 1);
  assert.match(otaBlock, /reverse_proxy xiaozhi-server:8003/);
  assert.doesNotMatch(otaBlock, /redir/);
  const ws = caddy.slice(caddy.indexOf("ws.nexus.warehouse-13.biz {"));
  assert.match(ws, /reverse_proxy xiaozhi-server:8000/);
  assert.match(ws, /versions 1\.1/);
});

test("hostname migration does not change Assessment Engine or Revel execute", () => {
  assert.match(engine, /await run_for_agent\(db, agent_id, for_date\)/);
  assert.doesNotMatch(engine, /revel_write|sendDeviceCommand/);
  assert.match(envExample, /^REVEL_EXECUTE_ENABLED=false$/m);
});
