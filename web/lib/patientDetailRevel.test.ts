import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  loadRevelStatus,
  revelContextCardModel,
  revelDiscussionContext,
  revelStatusPath,
  type RevelStatus,
} from "./revelStatus.ts";

const here = dirname(fileURLToPath(import.meta.url));
const detail = readFileSync(
  join(here, "../app/patients/[id]/PatientDetailClient.tsx"),
  "utf8",
);
const badge = readFileSync(
  join(here, "../components/RevelStatusBadge.tsx"),
  "utf8",
);
const contextCard = readFileSync(
  join(here, "../components/RevelContextCard.tsx"),
  "utf8",
);

/** Live CareConnect status: topic tag resolved, no player mapping. */
const LIVE_PARENT_STATUS = {
  tag: "care_overview",
  autoTrigger: true,
  device: null,
} as const;

function livePayload(partial: Partial<RevelStatus> = {}): RevelStatus {
  return {
    ok: true,
    enabled: false,
    mode: "off",
    autoTrigger: LIVE_PARENT_STATUS.autoTrigger,
    tag: LIVE_PARENT_STATUS.tag,
    deviceKey: null,
    device: LIVE_PARENT_STATUS.device,
    lastEvent: null,
    header: "Revel: OFF | Tag: care_overview",
    revelExecuteEnabled: false,
    ...partial,
  };
}

test("PatientDetailClient owns GET /agent/{id}/revel/status for the header badge, not transcript cards", () => {
  assert.match(detail, /loadRevelStatus\(id, apiGet\)/);
  assert.match(detail, /\[id, knowledgeTick\]/);
  assert.match(detail, /status=\{revelStatus\}/);
  assert.match(detail, /loading=\{revelStatusLoading\}/);
  assert.match(detail, /error=\{revelStatusError\}/);
  assert.doesNotMatch(detail, /onStatus=/);
  assert.doesNotMatch(detail, /setRevelStatus\)\s*\/>/);
  assert.doesNotMatch(detail, /patientDetailConversationRevel\(revelStatus\)/);
  assert.doesNotMatch(badge, /apiGet/);
  assert.doesNotMatch(badge, /onStatus/);
  assert.doesNotMatch(badge, /useEffect/);
  assert.doesNotMatch(badge, /agentId/);
  assert.match(badge, /Parent owns Revel status fetching/);
});

test("parent-owned live status drives the header badge, not a synthetic conversation card", async () => {
  const agentId = "agt_live";
  const calls: string[] = [];
  const get = async <T>(path: string): Promise<T> => {
    calls.push(path);
    assert.equal(path, revelStatusPath(agentId));
    assert.equal(path, `/agent/${agentId}/revel/status`);
    return livePayload() as T;
  };

  const result = await loadRevelStatus(agentId, get);
  assert.deepEqual(calls, [`/agent/${agentId}/revel/status`]);
  assert.equal(result.error, null);
  assert.ok(result.status);
  assert.equal(result.status.tag, "care_overview");
  assert.equal(result.status.autoTrigger, true);
  assert.equal(result.status.device, null);
  assert.match(result.status.header, /Tag: care_overview/);

  assert.match(detail, /buildConversationTimeline\(messages\)/);
  assert.doesNotMatch(detail, /buildConversationTimeline\(messages, revelContext\)/);
  assert.match(detail, /RevelContextCard context=\{item\.context\}/);
  assert.match(detail, /data-testid="revel-context-item"/);
  assert.match(detail, /data-testid="conversation-timeline"/);
  assert.doesNotMatch(detail, /gi === 0 && revelContext/);
  assert.match(contextCard, /revelContextCardModel\(context\)/);
  assert.match(contextCard, /\{card\.title\}/);
  assert.match(contextCard, /card\.rows\.map/);
});

test("persisted REVEL CONTEXT card model does not require configured, enabled, device mapping, auth, execute, or a display event", () => {
  const context = revelDiscussionContext({
    tag: "care_overview",
    autoTrigger: true,
    device: null,
  });
  assert.ok(context);
  const card = revelContextCardModel(context);
  assert.equal(card.title, "REVEL CONTEXT");
  assert.deepEqual(
    card.rows.map((row) => [row.label, row.value]),
    [
      ["Tag", "care_overview"],
      ["Auto Trigger", "Enabled"],
    ],
  );
  assert.equal(card.rows.some((row) => row.label === "Display"), false);
});

test("authenticated status failure leaves the header without inventing transcript REVEL CONTEXT", async () => {
  const get = async <T>(_path: string): Promise<T> => {
    throw new Error("Revel status unavailable");
  };
  const result = await loadRevelStatus("agt_live", get);
  assert.equal(result.status, null);
  assert.equal(result.error, "Revel status unavailable");
  assert.doesNotMatch(detail, /patientDetailConversationRevel\(revelStatus\)/);
  assert.match(detail, /buildConversationTimeline\(messages\)/);
});

test("DISPLAY EVENT stays a separate conversation card from persisted REVEL CONTEXT", () => {
  assert.match(detail, /RevelDisplayEvent/);
  assert.match(detail, /data-testid="revel-timeline-item"/);
  assert.match(detail, /item\.type === "revel_display"/);
  assert.match(detail, /item\.type === "revel_context"/);
  assert.doesNotMatch(contextCard, /Display event/);
  assert.doesNotMatch(contextCard, /DRY RUN/);
});
