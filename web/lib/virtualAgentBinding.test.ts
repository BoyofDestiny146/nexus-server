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
  assert.equal(page.includes("onViewSamDiscussion"), true);
  assert.equal(section.includes("Attach Virtual Agent"), true);
  assert.equal(section.includes("View Discussion"), true);
  assert.equal(section.includes("Unbind"), true);
  assert.equal(section.includes("EMPTY_VIRTUAL_AGENT_DISCUSSION"), true);
  assert.equal(section.includes("askSamReviewPath"), true);
  assert.equal(section.includes("pickSamSessionForView"), true);
  assert.equal(section.includes("onViewSamDiscussion"), true);
  assert.equal(section.includes("virtualAgentSessionsPath"), false);
  assert.equal(section.includes("cc_virtual_agent_message"), false);
  assert.equal(section.includes("VirtualAgentDiscussionModal"), false);
  assert.equal(section.includes("embedded"), true);
  assert.equal(section.includes("/sam/conversation"), false);
  assert.equal(section.includes("ai_device"), false);
});

test("unified Agents section covers empty, VA-only, solid-only, and both", () => {
  const page = readFileSync(new URL("../app/patients/[id]/PatientDetailClient.tsx", import.meta.url), "utf8");
  const section = readFileSync(new URL("../components/VirtualAgentBindings.tsx", import.meta.url), "utf8");
  const agentsStart = page.indexOf("<SectionLabel>Agents</SectionLabel>");
  const agentsEnd = page.indexOf("<AskSamDiscussions", agentsStart);
  assert.ok(agentsStart >= 0 && agentsEnd > agentsStart);
  const agents = page.slice(agentsStart, agentsEnd);
  // empty: combined copy only when both solid and VA counts are zero
  assert.match(agents, /devices\.length === 0 && virtualBoundCount === 0/);
  assert.match(agents, /No Agents bound to this client\./);
  // solid-only / both: solid cards gated on devices.length > 0
  assert.match(agents, /devices\.length > 0/);
  assert.match(agents, />Solid</);
  // VA-only / both: embedded bindings keep cards + Discussion/Unbind; hide VA chrome empty
  assert.match(agents, /embedded/);
  assert.match(agents, /onBoundChange=\{/);
  assert.match(section, /!embedded && bound && bound\.length === 0/);
  assert.match(section, /View Discussion/);
  assert.match(section, /Unbind/);
  assert.match(section, />Virtual</);
});

test("Edit Client uses Attach Agent wording while keeping Watcher-specific recording copy", () => {
  const edit = readFileSync(new URL("../components/EditClientWorkspace.tsx", import.meta.url), "utf8");
  const form = readFileSync(new URL("./clientForm.ts", import.meta.url), "utf8");
  assert.match(form, /key: "device", label: "Agents"/);
  assert.match(edit, /Attach Agent/);
  assert.match(edit, /existing Attach Agent control/);
  assert.doesNotMatch(edit, /Attach a Watcher/);
  assert.doesNotMatch(edit, /Attach device control/);
  assert.doesNotMatch(edit, /existing Device controls/);
  // genuinely Watcher-specific
  assert.match(edit, /No Watcher is bound to this client\. Conversations will not be recorded/);
  assert.match(edit, /SenseCAP Watcher/);
  assert.match(edit, /Device ID/);
  assert.match(edit, /Bound Watchers inherit these/);
});
