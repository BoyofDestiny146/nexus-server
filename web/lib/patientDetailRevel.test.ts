import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  conversationRendersRevelContext,
  loadRevelStatus,
  patientDetailConversationRevel,
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

test("PatientDetailClient owns GET /agent/{id}/revel/status and does not take status from the badge", () => {
  assert.match(detail, /loadRevelStatus\(id, apiGet\)/);
  assert.match(detail, /\[id, knowledgeTick\]/);
  assert.match(detail, /patientDetailConversationRevel\(revelStatus\)/);
  assert.match(detail, /status=\{revelStatus\}/);
  assert.match(detail, /loading=\{revelStatusLoading\}/);
  assert.match(detail, /error=\{revelStatusError\}/);
  assert.doesNotMatch(detail, /onStatus=/);
  assert.doesNotMatch(detail, /setRevelStatus\)\s*\/>/);
  assert.doesNotMatch(badge, /apiGet/);
  assert.doesNotMatch(badge, /onStatus/);
  assert.doesNotMatch(badge, /useEffect/);
  assert.doesNotMatch(badge, /agentId/);
  assert.match(badge, /Parent owns Revel status fetching/);
});

test("parent-owned live status {tag:care_overview, autoTrigger:true, device:null} renders REVEL CONTEXT in the conversation", async () => {
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

  // Same function PatientDetailClient uses for conversation chrome.
  const conversation = patientDetailConversationRevel(result.status);
  assert.ok(conversation.revelContext);
  assert.ok(conversation.card);
  assert.match(conversation.card, /^REVEL CONTEXT$/m);
  assert.match(conversation.card, /care_overview/);
  assert.match(conversation.card, /Enabled/);
  assert.doesNotMatch(conversation.card, /^Display\b/m);
  assert.doesNotMatch(conversation.card, /DISPLAY EVENT/);

  const rendered = conversationRendersRevelContext(result.status);
  assert.equal(rendered, conversation.card);
  assert.match(rendered ?? "", /REVEL CONTEXT/);
  assert.match(rendered ?? "", /Tag\s+care_overview/);
  assert.match(rendered ?? "", /Auto Trigger\s+Enabled/);

  const card = revelContextCardModel(conversation.revelContext);
  assert.equal(card.title, "REVEL CONTEXT");
  assert.deepEqual(
    card.rows.map((row) => [row.label, row.value]),
    [
      ["Tag", "care_overview"],
      ["Auto Trigger", "Enabled"],
    ],
  );
  assert.equal(card.rows.some((row) => row.label === "Display"), false);

  assert.match(detail, /RevelContextCard context=\{revelContext\}/);
  assert.match(detail, /data-testid="revel-context-item"/);
  assert.match(contextCard, /revelContextCardModel\(context\)/);
  assert.match(contextCard, /\{card\.title\}/);
  assert.match(contextCard, /card\.rows\.map/);
});

test("REVEL CONTEXT does not require configured, enabled, device mapping, auth, execute, or a display event", () => {
  const status = {
    tag: "care_overview",
    autoTrigger: true,
    device: null,
  };
  const conversation = patientDetailConversationRevel(status);
  assert.ok(conversation.card);
  assert.match(conversation.card, /REVEL CONTEXT/);
  assert.match(conversation.card, /care_overview/);
  assert.match(conversation.card, /Enabled/);
  assert.doesNotMatch(conversation.card, /Display/);
  assert.equal("configured" in status, false);
  assert.equal("enabled" in status, false);
  assert.equal("revelExecuteEnabled" in status, false);
});

test("authenticated status failure leaves the conversation without fabricated REVEL CONTEXT", async () => {
  const get = async <T>(_path: string): Promise<T> => {
    throw new Error("Revel status unavailable");
  };
  const result = await loadRevelStatus("agt_live", get);
  assert.equal(result.status, null);
  assert.equal(result.error, "Revel status unavailable");

  const conversation = patientDetailConversationRevel(result.status);
  assert.equal(conversation.revelContext, null);
  assert.equal(conversation.card, null);
  assert.equal(conversationRendersRevelContext(result.status), null);
  assert.equal(revelDiscussionContext(result.status), null);
});

test("DISPLAY EVENT stays a separate conversation card from parent-owned REVEL CONTEXT", () => {
  assert.match(detail, /RevelDisplayEvent/);
  assert.match(detail, /data-testid="revel-timeline-item"/);
  assert.doesNotMatch(contextCard, /Display event/);
  assert.doesNotMatch(contextCard, /DRY RUN/);
  const conversation = patientDetailConversationRevel(LIVE_PARENT_STATUS);
  assert.doesNotMatch(conversation.card ?? "", /DISPLAY EVENT/);
});
