/** Portal AI Agents. Software agents are created in this organization. Not an agent id. */
export const PORTAL_AI_AGENTS_ORGANIZATION_ID = "d51af0ec95ac4b4a949f8d5b64cdd30a";

/**
 * Control-page connection status. Integration connectivity is not probed from
 * the roster; never report Online from Virtual Agents UI.
 */
export const VIRTUAL_AGENT_STATUS = "Not verified";

/** Roster Status from reliable bind state already on the row. Never Online. */
export function virtualAgentRosterStatus(boundClientId?: string | null): string {
  return (boundClientId || "").trim() ? "Bound" : "Unattached";
}

export interface VirtualAgentCandidate {
  id: string;
  organizationId?: string | null;
  deviceCount?: number | null;
  /** True when this record is attached to a care client as the virtual agent. */
  boundAsVirtualAgent?: boolean;
  /** True when a virtual agent is attached to this record. */
  boundAsClient?: boolean;
  /** Care client id from cc_virtual_agent_binding when this row is the VA side. */
  boundClientId?: string | null;
  /** Care client agent_name from the bound client row — not this VA's agent_name. */
  boundClientName?: string | null;
}

/** Roster Client column when no care client is bound. */
export const UNATTACHED_CLIENT_LABEL = "Unattached";

/**
 * A Watcher is a care client. A bound virtual agent stays a virtual agent.
 * A record that is only the client side of a binding stays a client, even
 * with no Watcher. An unbound member of the software-agent organization is
 * a virtual agent waiting to be attached.
 */
export function isVirtualAgent(agent: VirtualAgentCandidate): boolean {
  if ((agent.deviceCount ?? 0) !== 0) return false;
  if (agent.boundAsVirtualAgent) return true;
  if (agent.boundAsClient) return false;
  return agent.organizationId === PORTAL_AI_AGENTS_ORGANIZATION_ID;
}

/** Care-client roster. Virtual agents stay on the Virtual Agents page. */
export function visibleClientRecords<T extends { virtualAgent?: boolean }>(agents: T[]): T[] {
  return agents.filter((agent) => agent.virtualAgent !== true);
}

export function selectVirtualAgents<T extends VirtualAgentCandidate>(agents: T[]): T[] {
  const seen = new Set<string>();
  const selected: T[] = [];
  for (const agent of agents) {
    if (!isVirtualAgent(agent) || seen.has(agent.id)) continue;
    seen.add(agent.id);
    selected.push(agent);
  }
  return selected;
}

export function clientDetailHref(clientId: string): string {
  return `/patients/${clientId}`;
}

/** Static route. A query id avoids a new Caddy fallback for dynamic segments. */
export function virtualAgentControlHref(clientId: string): string {
  return `/virtual-agents/control/?id=${encodeURIComponent(clientId)}`;
}

/** Create form route. Does not auto-bind after save. */
export function virtualAgentNewHref(): string {
  return "/virtual-agents/new";
}

/** Dedicated create endpoint. Same path as the attachable-VA list (POST vs GET). */
export function virtualAgentCreatePath(): string {
  return "/agent/virtual";
}

export function createVirtualAgentPayload(name: string): { name: string } {
  return { name: name.trim() };
}

export function parseVirtualAgentControlId(search: string): string | null {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const id = (params.get("id") || "").trim();
  if (!id || id === "_") return null;
  return id;
}

export const INTEGRATION_PRODUCT = "BridgingCare / Ask Sam";
export const CONVERSATION_REVIEW_NOTICE = "Conversation review is not yet available.";
export const LAST_VERIFIED_COMMUNICATION = "Not available";
export const AUTHENTICATION_STATUS = "Not verifiable from this dashboard";

export const SAM_PERMISSIONS = [
  "Read-only knowledge assistance",
  "No alarm-rule modifications",
  "No autonomous device control",
  "BridgingCare administrator authorization",
  "Knowledge restricted by server allowlist and client assignment",
] as const;

export function personalitySummary(input: {
  personalityId?: string | null;
  personalityName?: string | null;
  personaOverride?: string | null;
}): { title: string; detail: string } {
  const title = input.personalityName?.trim() || input.personalityId?.trim() || "Not set on the client record";
  const detail = input.personaOverride?.trim() || "No additional persona note is stored on the client record.";
  return { title, detail };
}

export interface VirtualAgentRow {
  id: string;
  agentName: string;
  /** Care client id for the Client link; null when unbound. */
  clientId: string | null;
  /** Care client display name; never the virtual agent's own agent_name. */
  clientName: string;
  organizationName: string;
  aiModel: string;
  inheritsDefault: boolean;
  serverModel: string | null;
  modelWarning?: string | null;
  knowledge: string;
  status: string;
}

export interface AskSamModelStatus {
  storedModel: string | null;
  serverModel: string | null;
  effectiveModel: string | null;
  isActiveIntegrationAgent: boolean;
}

/** Saved override. An empty profile inherits the server default and is not written. */
export function assignedModelLabel(storedModel: string | null | undefined): string {
  const text = (storedModel || "").trim();
  return text || "Server Default (no override)";
}

export function assignedModelNote(storedModel: string | null | undefined): string {
  if ((storedModel || "").trim()) {
    return "Saved on this virtual agent. Ask Sam uses it when this agent is the active integration agent.";
  }
  return "No override is saved. The server default is not written onto this agent.";
}

/** Nexus server default. The value comes from the API, not from this file. */
export function serverModelLabel(serverModel: string | null | undefined): string {
  const text = (serverModel || "").trim();
  return text || "Not configured";
}

/** Same tag as the server default. An override does not replace this value. */
export function inheritedModelLabel(serverModel: string | null | undefined): string {
  return serverModelLabel(serverModel);
}

/**
 * Roster label. A saved tag is shown as itself. Otherwise the server default
 * is named, and no other installed model is chosen in its place.
 */
export function rosterModelLabel(
  storedModel: string | null | undefined,
  serverModel: string | null | undefined,
): string {
  const saved = (storedModel || "").trim();
  if (saved) return saved;
  const inherited = (serverModel || "").trim();
  if (!inherited) return "Not configured";
  return `${inherited} (Server Default)`;
}

export type ModelCatalogState = "loading" | "unavailable" | readonly string[];

/** Warn when the configured default cannot be confirmed. Never names a substitute. */
export function serverDefaultWarning(
  serverModel: string | null | undefined,
  catalog: ModelCatalogState,
): string | null {
  if (catalog === "loading") return null;
  const server = (serverModel || "").trim();
  if (!server) return "The server default is not configured. No other model was selected.";
  if (catalog === "unavailable") {
    return "The server default could not be checked. No other model was selected.";
  }
  if (!catalog.some((name) => name.trim() === server)) {
    return "The server default is not available. No other model was selected.";
  }
  return null;
}

/**
 * Model the Ask Sam route is using for this agent. A saved tag is not serving
 * until that route reads it. Today only the active integration agent is served,
 * and it is served with the server default.
 */
export function servingModelLabel(effectiveModel: string | null | undefined): string {
  const text = (effectiveModel || "").trim();
  return text || "Not serving";
}

export function activeIntegrationLabel(active: boolean): "Yes" | "No" {
  return active ? "Yes" : "No";
}

/** Table row from existing client and knowledge records.

 * Agent Name uses bot_name. Client uses the bound care client from
 * cc_virtual_agent_binding — never the virtual agent's own agent_name.
 */
export function toVirtualAgentRow(input: {
  id: string;
  botName: string;
  organizationName: string;
  knowledge: string;
  storedModel?: string | null;
  serverModel?: string | null;
  boundClientId?: string | null;
  boundClientName?: string | null;
}): VirtualAgentRow {
  const stored = (input.storedModel || "").trim();
  const server = (input.serverModel || "").trim() || null;
  const clientId = (input.boundClientId || "").trim() || null;
  const clientName = clientId
    ? ((input.boundClientName || "").trim() || clientId)
    : UNATTACHED_CLIENT_LABEL;
  return {
    id: input.id,
    agentName: input.botName,
    clientId,
    clientName,
    organizationName: input.organizationName,
    aiModel: rosterModelLabel(stored, server),
    inheritsDefault: stored === "",
    serverModel: server,
    knowledge: input.knowledge,
    status: virtualAgentRosterStatus(clientId),
  };
}

export function filterVirtualAgentRows(rows: VirtualAgentRow[], query: string): VirtualAgentRow[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return rows;
  return rows.filter((row) =>
    [row.agentName, row.clientName, row.organizationName, row.aiModel, row.knowledge, row.status]
      .join("\n")
      .toLowerCase()
      .includes(needle),
  );
}

export function knowledgeAssignmentLabel(
  bases: Array<{ id: number; name: string; assignmentEnabled?: boolean }>,
): string {
  const assigned = bases.filter((base) => base.assignmentEnabled !== false && base.name.trim() !== "");
  if (assigned.length === 0) return "No knowledge base assigned";
  return assigned.map((base) => `${base.name} (ID ${base.id})`).join(", ");
}

