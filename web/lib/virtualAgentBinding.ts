export interface VirtualAgentChoice {
  id: string;
  agentName?: string | null;
  botName?: string | null;
}

export interface BoundVirtualAgent {
  clientId: string;
  virtualAgentId: string;
  agentName?: string | null;
  botName?: string | null;
  organizationId?: string | null;
  createdAt?: string | null;
}

export interface VirtualAgentTurn {
  id: number;
  question: string;
  answer: string;
  followUp?: string | null;
  createdAt?: string | null;
}

export const VIRTUAL_AGENTS_PATH = "/agent/virtual";

export const EMPTY_VIRTUAL_AGENTS = "No virtual agent is associated with this client.";

export const EMPTY_VIRTUAL_AGENT_DISCUSSION = "No discussion is stored for this virtual agent.";

export const EMPTY_AVAILABLE_VIRTUAL_AGENTS = "No virtual agents are available to attach.";

export function clientVirtualAgentsPath(clientId: string): string {
  return `/agent/${clientId}/virtual-agents`;
}

export function clientVirtualAgentPath(clientId: string, virtualAgentId: string): string {
  return `/agent/${clientId}/virtual-agents/${virtualAgentId}`;
}

export function virtualAgentSessionsPath(clientId: string, virtualAgentId: string): string {
  return `/agent/${clientId}/virtual-agents/${virtualAgentId}/sessions`;
}

export function virtualAgentSessionPath(clientId: string, virtualAgentId: string, sessionId: string): string {
  return `/agent/${clientId}/virtual-agents/${virtualAgentId}/sessions/${encodeURIComponent(sessionId)}`;
}

export function bindVirtualAgentPayload(virtualAgentId: string): { virtualAgentId: string } {
  return { virtualAgentId };
}

export function virtualAgentLabel(agent: { agentName?: string | null; botName?: string | null; virtualAgentId?: string }): string {
  const bot = (agent.botName || "").trim();
  const name = (agent.agentName || "").trim();
  return bot || name || agent.virtualAgentId || "Virtual agent";
}

/** Hide the open client, agents already attached, and repeated ids. */
export function selectableVirtualAgents<T extends { id: string }>(
  available: T[],
  clientId: string,
  boundIds: string[],
): T[] {
  const bound = new Set(boundIds);
  const seen = new Set<string>();
  const selected: T[] = [];
  for (const agent of available) {
    if (!agent.id || agent.id === clientId || bound.has(agent.id) || seen.has(agent.id)) continue;
    seen.add(agent.id);
    selected.push(agent);
  }
  return selected;
}

export function virtualAgentTurnFollowUp(turn: Pick<VirtualAgentTurn, "followUp">): string {
  return (turn.followUp || "").trim();
}
