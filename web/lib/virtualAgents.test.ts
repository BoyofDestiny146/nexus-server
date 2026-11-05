import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  CONVERSATION_REVIEW_NOTICE,
  PORTAL_AI_AGENTS_ORGANIZATION_ID,
  UNATTACHED_CLIENT_LABEL,
  VIRTUAL_AGENT_STATUS,
  activeIntegrationLabel,
  assignedModelLabel,
  inheritedModelLabel,
  rosterModelLabel,
  serverDefaultWarning,
  clientDetailHref,
  filterVirtualAgentRows,
  knowledgeAssignmentLabel,
  parseVirtualAgentControlId,
  personalitySummary,
  selectVirtualAgents,
  serverModelLabel,
  servingModelLabel,
  toVirtualAgentRow,
  createVirtualAgentPayload,
  virtualAgentControlHref,
  virtualAgentCreatePath,
  virtualAgentNewHref,
  virtualAgentRosterStatus,
  visibleClientRecords,
} from "./virtualAgents.ts";

const here = dirname(fileURLToPath(import.meta.url));
const PORTAL_CLIENT = "portal-client";
const SAM_AGENT = "sam-agent";

const roster = [
  { id: "bill", agentName: "Bill Bob", organizationId: "ff6ab2bac2b147c080bce9ecbfd0d49e", deviceCount: 2 },
  { id: "dalton", agentName: "B Dalton", organizationId: "ff6ab2bac2b147c080bce9ecbfd0d49e", deviceCount: 2 },
  {
    id: PORTAL_CLIENT,
    agentName: "Care Client",
    organizationId: PORTAL_AI_AGENTS_ORGANIZATION_ID,
    deviceCount: 0,
    boundAsClient: true,
  },
  {
    id: PORTAL_CLIENT,
    agentName: "Care Client",
    organizationId: PORTAL_AI_AGENTS_ORGANIZATION_ID,
    deviceCount: 0,
    boundAsClient: true,
  },
  {
    id: SAM_AGENT,
    agentName: "Sam",
    organizationId: PORTAL_AI_AGENTS_ORGANIZATION_ID,
    deviceCount: 0,
    boundAsVirtualAgent: true,
  },
  {
    id: "unbound-agent",
    agentName: "Unbound",
    organizationId: PORTAL_AI_AGENTS_ORGANIZATION_ID,
    deviceCount: 0,
  },
  {
    id: "watcher-in-portal-org",
    agentName: "Bound Watcher",
    organizationId: PORTAL_AI_AGENTS_ORGANIZATION_ID,
    deviceCount: 1,
  },
  {
    id: "device-free-client",
    agentName: "No Watcher Elsewhere",
    organizationId: "ff6ab2bac2b147c080bce9ecbfd0d49e",
    deviceCount: 0,
  },
];

test("virtual agents are bound software agents, not every device-free client", () => {
  const selected = selectVirtualAgents(roster);
  assert.deepEqual(selected.map((agent) => agent.id), [SAM_AGENT, "unbound-agent"]);
});

test("the clients roster keeps care clients and hides functioning virtual agents", () => {
  const visible = visibleClientRecords([
    { id: PORTAL_CLIENT, virtualAgent: false },
    { id: SAM_AGENT, virtualAgent: true },
    { id: "bill", virtualAgent: false },
    { id: "legacy" },
  ]);
  assert.deepEqual(visible.map((agent) => agent.id), [PORTAL_CLIENT, "bill", "legacy"]);
});

test("model labels come from server fields and a saved tag is not described as serving", () => {
  assert.equal(assignedModelLabel(null), "Server Default (no override)");
  assert.equal(assignedModelLabel("other-model"), "other-model");
  assert.equal(rosterModelLabel(null, "server-model"), "server-model (Server Default)");
  assert.equal(rosterModelLabel("other-model", "server-model"), "other-model");
  assert.equal(rosterModelLabel(null, null), "Not configured");
  assert.equal(inheritedModelLabel("server-model"), "server-model");
  assert.equal(serverModelLabel("server-model"), "server-model");
  assert.equal(servingModelLabel(null), "Not serving");
  assert.equal(servingModelLabel("server-model"), "server-model");
  assert.equal(activeIntegrationLabel(true), "Yes");
  assert.equal(activeIntegrationLabel(false), "No");
  const missing = serverDefaultWarning("server-model", ["other-model", "triage-model"]);
  assert.match(missing || "", /not available/);
  assert.doesNotMatch(missing || "", /other-model|triage-model/);
  assert.equal(serverDefaultWarning("server-model", ["server-model", "other-model"]), null);
  assert.equal(serverDefaultWarning("server-model", "loading"), null);
  assert.match(serverDefaultWarning("server-model", "unavailable") || "", /could not be checked/);
  assert.equal(VIRTUAL_AGENT_STATUS, "Not verified");
  assert.notEqual(VIRTUAL_AGENT_STATUS.toLowerCase(), "online");
  assert.equal(virtualAgentRosterStatus(null), "Unattached");
  assert.equal(virtualAgentRosterStatus(PORTAL_CLIENT), "Bound");
  assert.notEqual(virtualAgentRosterStatus(PORTAL_CLIENT).toLowerCase(), "online");
  assert.equal(clientDetailHref(PORTAL_CLIENT), `/patients/${PORTAL_CLIENT}`);
});

test("knowledge label uses the assigned base name and id", () => {
  assert.equal(
    knowledgeAssignmentLabel([{ id: 3, name: "Portal Alarm Documentation", assignmentEnabled: true }]),
    "Portal Alarm Documentation (ID 3)",
  );
  assert.equal(
    knowledgeAssignmentLabel([{ id: 1, name: "Bio-EV Sales", assignmentEnabled: false }]),
    "No knowledge base assigned",
  );
});

test("a virtual agent row shows the bound care client, not the VA agent_name", () => {
  const row = toVirtualAgentRow({
    id: SAM_AGENT,
    botName: "Sam Virtual Portal Agent",
    organizationName: "Portal AI Agents",
    knowledge: "Portal Alarm Documentation (ID 3)",
    storedModel: null,
    serverModel: "server-model",
    boundClientId: PORTAL_CLIENT,
    boundClientName: "Portal-US",
  });
  assert.equal(row.agentName, "Sam Virtual Portal Agent");
  assert.equal(row.clientId, PORTAL_CLIENT);
  assert.equal(row.clientName, "Portal-US");
  assert.notEqual(row.clientName, "Sam");
  assert.equal(row.aiModel, "server-model (Server Default)");
  assert.equal(row.inheritsDefault, true);
  assert.equal(row.status, "Bound");
  const unbound = toVirtualAgentRow({
    id: "unbound-agent",
    botName: "Unbound Agent",
    organizationName: "Portal AI Agents",
    knowledge: "No knowledge base assigned",
  });
  assert.equal(unbound.clientId, null);
  assert.equal(unbound.clientName, UNATTACHED_CLIENT_LABEL);
  assert.equal(unbound.status, "Unattached");
  const saved = toVirtualAgentRow({
    id: SAM_AGENT,
    botName: row.agentName,
    organizationName: row.organizationName,
    knowledge: row.knowledge,
    storedModel: "other-model",
    serverModel: "server-model",
    boundClientId: PORTAL_CLIENT,
    boundClientName: "Portal-US",
  });
  assert.equal(saved.aiModel, "other-model");
  assert.equal(saved.inheritsDefault, false);
  assert.equal(saved.clientName, "Portal-US");
  const cleared = toVirtualAgentRow({
    id: SAM_AGENT,
    botName: saved.agentName,
    organizationName: saved.organizationName,
    knowledge: saved.knowledge,
    storedModel: null,
    serverModel: "server-model",
    boundClientId: PORTAL_CLIENT,
    boundClientName: "Portal-US",
  });
  assert.equal(cleared.aiModel, "server-model (Server Default)");
  assert.equal(filterVirtualAgentRows([saved], "other-model").length, 1);
  assert.equal(filterVirtualAgentRows([row], "Portal-US").length, 1);
  assert.equal(filterVirtualAgentRows([row], "Sam Virtual Portal Agent").length, 1);
  assert.equal(filterVirtualAgentRows([row], "Betty").length, 0);
  assert.equal(filterVirtualAgentRows([row], "  ").length, 1);
});

test("control opens a static panel and saves a model without hardcoding an agent or tag", () => {
  assert.equal(
    virtualAgentControlHref(SAM_AGENT),
    `/virtual-agents/control/?id=${SAM_AGENT}`,
  );
  assert.equal(parseVirtualAgentControlId(`?id=${SAM_AGENT}`), SAM_AGENT);
  assert.equal(parseVirtualAgentControlId(""), null);
  const personality = personalitySummary({
    personalityId: "sys_witty_tech_sidekick",
    personalityName: "The Witty Tech Sidekick",
    personaOverride: null,
  });
  assert.equal(personality.title, "The Witty Tech Sidekick");
  assert.match(personality.detail, /No additional persona note/);
  const page = readFileSync(join(here, "../app/virtual-agents/page.tsx"), "utf8");
  const control = readFileSync(join(here, "../app/virtual-agents/control/page.tsx"), "utf8");
  const helper = readFileSync(join(here, "virtualAgents.ts"), "utf8");
  const patients = readFileSync(join(here, "../app/patients/page.tsx"), "utf8");
  assert.match(page, /virtualAgentControlHref/);
  assert.equal(CONVERSATION_REVIEW_NOTICE, "Conversation review is not yet available.");
  assert.match(control, /ConversationReview/);
  assert.match(control, /agentSamReviewPath\(agentId\)/);
  assert.doesNotMatch(control, /CONVERSATION_REVIEW_NOTICE/);
  assert.match(control, /Assigned model/);
  assert.match(control, /Inherited model/);
  assert.match(control, /Serving model/);
  assert.match(control, /serverDefaultWarning/);
  assert.match(control, /model\.serverModel/);
  assert.match(control, /Model availability/);
  assert.match(control, /apiPut<AskSamModelStatus>\(`\/agent\/\$\{agentId\}\/ask-sam-model`/);
  assert.match(control, /\/models\/ollama/);
  assert.doesNotMatch(control, /apiPatch|apiPost|apiDelete/);
  assert.match(control, /Open knowledge management/);
  assert.match(control, /clientDetailHref/);
  assert.match(control, /VIRTUAL_AGENT_STATUS/);
  assert.match(control, /SAM_PERMISSIONS/);
  assert.match(control, /isActiveIntegrationAgent/);
  assert.doesNotMatch(control, /VoiceSelector/);
  assert.doesNotMatch(control, /Online/);
  assert.doesNotMatch(control, /1c46373751d84d29a1aab382fd65501f/);
  assert.doesNotMatch(control, /62d1a3a6575f4bb8a0f3814f3e29a1ea/);
  assert.doesNotMatch(control, /qwen2\.5:3b/);
  assert.doesNotMatch(control, /SAM_PORTAL_CLIENT_ID/);
  assert.doesNotMatch(helper, /1c46373751d84d29a1aab382fd65501f/);
  assert.doesNotMatch(helper, /qwen2\.5:3b/);
  assert.doesNotMatch(helper, /SAM_PORTAL_CLIENT_ID/);
  assert.match(patients, /visibleClientRecords/);
});

test("Virtual Agents sits between Clients and Agents and uses the devices table structure", () => {
  const shell = readFileSync(join(here, "../components/AppShell.tsx"), "utf8");
  const page = readFileSync(join(here, "../app/virtual-agents/page.tsx"), "utf8");
  const devices = readFileSync(join(here, "../app/devices/page.tsx"), "utf8");
  const clients = shell.indexOf(">Clients<");
  const virtual = shell.indexOf('label="Virtual Agents"');
  const mainNav = shell.indexOf("{MAIN_NAV.map");
  assert.ok(clients >= 0 && virtual > clients && mainNav > virtual);
  assert.match(shell, /href: "\/devices", label: "Solid Agents"/);
  assert.doesNotMatch(shell, /label: "Devices"/);
  assert.match(shell, /href="\/virtual-agents"/);
  assert.match(page, /filterVirtualAgentRows/);
  assert.match(page, /toVirtualAgentRow/);
  assert.match(page, /storedModel: detail\.storedModel/);
  assert.match(page, /serverModel: detail\.serverModel/);
  assert.match(page, /boundClientId: agent\.boundClientId/);
  assert.match(page, /boundClientName: agent\.boundClientName/);
  assert.match(page, /clientDetailHref\(agent\.clientId\)/);
  assert.doesNotMatch(page, /clientDetailHref\(agent\.id\)/);
  assert.match(page, /serverDefaultWarning/);
  assert.match(page, /Agent Name[\s\S]*Client[\s\S]*Organization[\s\S]*AI Model[\s\S]*Knowledge[\s\S]*Status[\s\S]*Control/);
  assert.doesNotMatch(page, /Online/);
  assert.doesNotMatch(page, /VoiceSelector/);
  assert.doesNotMatch(page, /macAddress/);
  assert.match(page, /clientDetailHref/);
  assert.match(page, /\/agent\/list\?organizationId=/);
  assert.match(page, /\/knowledge-bases/);
  assert.doesNotMatch(page, /qwen2\.5:3b/);
  assert.match(page, /virtualAgentNewHref/);
  assert.match(page, /\+ Add Virtual Agent/);
  assert.match(devices, /title="Solid Agents"/);
  assert.match(devices, /Physical agents on this server/);
  assert.match(devices, /MAC \/ EUI/);
  assert.match(devices, /VoiceSelector/);
  assert.match(devices, /\/admin\/device\/all/);
});

test("Add Virtual Agent creates via POST /agent/virtual without auto-bind", () => {
  assert.equal(virtualAgentNewHref(), "/virtual-agents/new");
  assert.equal(virtualAgentCreatePath(), "/agent/virtual");
  assert.deepEqual(createVirtualAgentPayload("  Desk Agent  "), { name: "Desk Agent" });
  const createPage = readFileSync(join(here, "../app/virtual-agents/new/page.tsx"), "utf8");
  assert.match(createPage, /virtualAgentCreatePath\(\)/);
  assert.match(createPage, /createVirtualAgentPayload\(name\)/);
  assert.match(createPage, /apiPost/);
  assert.match(createPage, /ask-sam-model/);
  assert.match(createPage, /knowledge-bases/);
  assert.match(createPage, /KnowledgeAccessFields/);
  assert.match(createPage, /virtualAgentControlHref/);
  assert.match(createPage, /Attach to Client/);
  assert.match(createPage, /clientVirtualAgentsPath/);
  assert.match(createPage, /bindVirtualAgentPayload/);
  assert.match(createPage, /visibleClientRecords/);
  assert.doesNotMatch(createPage, /apiPost\([^)]*onboard/);
  assert.doesNotMatch(createPage, /auto-bind|autoBind|auto attach/i);
  assert.doesNotMatch(createPage, /partner_bridgingcare|CC_SAM_CLIENT_ID/);
  assert.doesNotMatch(createPage, /1c46373751d84d29a1aab382fd65501f/);
});
