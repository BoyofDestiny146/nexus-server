import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  EMPTY_VIRTUAL_AGENT_DISCUSSION,
  EMPTY_VIRTUAL_AGENTS,
  bindVirtualAgentPayload,
  clientVirtualAgentsPath,
  selectableVirtualAgents,
  virtualAgentLabel,
  virtualAgentSessionPath,
  virtualAgentTurnFollowUp,
} from "./virtualAgentBinding.ts";

const SAM = "1c46373751d84d29a1aab382fd65501f";

test("binding payload points at an existing virtual-agent id", () => {
  assert.deepEqual(bindVirtualAgentPayload(SAM), { virtualAgentId: SAM });
  assert.equal(clientVirtualAgentsPath("bill"), "/agent/bill/virtual-agents");
  assert.equal(
    virtualAgentSessionPath("bill", SAM, "session a"),
    `/agent/bill/virtual-agents/${SAM}/sessions/session%20a`,
  );
});

test("a client cannot select itself or a virtual agent already attached", () => {
  const available = [
    { id: "bill", botName: "Bill Bob" },
    { id: SAM, botName: "Sam Virtual Portal Agent", agentName: "Portal-US" },
    { id: SAM, botName: "Sam Virtual Portal Agent" },
  ];
  assert.deepEqual(
    selectableVirtualAgents(available, "bill", []).map((item) => item.id),
    [SAM],
  );
  assert.deepEqual(
    selectableVirtualAgents(available, "other", [SAM]).map((item) => item.id),
    ["bill"],
  );
  assert.equal(virtualAgentLabel({ botName: "Sam Virtual Portal Agent", agentName: "Portal-US" }), "Sam Virtual Portal Agent");
});

test("an empty discussion stays empty", () => {
  assert.equal(EMPTY_VIRTUAL_AGENTS.length > 0, true);
  assert.equal(EMPTY_VIRTUAL_AGENT_DISCUSSION.length > 0, true);
  assert.equal(virtualAgentTurnFollowUp({ followUp: "  " }), "");
  assert.equal(virtualAgentTurnFollowUp({ followUp: "Which alarm?" }), "Which alarm?");
});

test("the client page consolidates Watchers and virtual agents under one Agents section", () => {
  const page = readFileSync(new URL("../app/patients/[id]/PatientDetailClient.tsx", import.meta.url), "utf8");
  const section = readFileSync(new URL("../components/VirtualAgentBindings.tsx", import.meta.url), "utf8");
  assert.equal(page.includes("Attach a Watcher"), true);
  assert.equal(page.includes("<SectionLabel>Agents</SectionLabel>"), true);
  assert.equal(page.includes("<SectionLabel>Devices</SectionLabel>"), false);
  assert.equal(page.includes("No Agents bound to this client."), true);
  assert.equal(page.includes("Attach Agent"), true);
  assert.equal(page.includes("AttachAgentChooserModal"), true);
  assert.equal(page.includes('data-testid="attach-agent-choose-solid"'), true);
  assert.equal(page.includes('data-testid="attach-agent-choose-virtual"'), true);
  assert.equal(page.includes("embedded"), true);
  assert.equal(page.includes("<VirtualAgentBindings"), true);
  assert.equal(section.includes("Attach Virtual Agent"), true);
  assert.equal(section.includes("View Discussion"), true);
  assert.equal(section.includes("Unbind"), true);
  assert.equal(section.includes("EMPTY_VIRTUAL_AGENT_DISCUSSION"), true);
  assert.equal(section.includes("embedded"), true);
  assert.equal(section.includes("/sam/conversation"), false);
  assert.equal(section.includes("ai_device"), false);
});
