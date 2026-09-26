import type { AgentDetail } from "@/lib/types";

export interface ClientFormDraft {
  name: string;
  dob: string | null;
  age: number | null;
  condition: string;
  tags: string[];
  botName: string;
  escalationPhrases: string[];
  topicsToAvoid: string[];
  personaOverride: string;
}

export const EMPTY_CLIENT_DRAFT: ClientFormDraft = {
  name: "",
  dob: null,
  age: null,
  condition: "",
  tags: [],
  botName: "",
  escalationPhrases: [],
  topicsToAvoid: [],
  personaOverride: "",
};

export const CLIENT_FORM_STEPS = [
  { key: "profile", label: "Profile" },
  { key: "guardrails", label: "Guardrails" },
  { key: "device", label: "Device" },
  { key: "review", label: "Review" },
] as const;

/** Edit Client only — Knowledge is assigned after the client exists. */
export const EDIT_CLIENT_FORM_STEPS = [
  { key: "profile", label: "Profile" },
  { key: "guardrails", label: "Guardrails" },
  { key: "device", label: "Device" },
  { key: "knowledge", label: "Knowledge" },
  { key: "review", label: "Review" },
] as const;

export const TAG_SUGGESTIONS = [
  "exercise-recommended", "low-sodium", "fall-risk",
  "mobility-aid", "medication-reminder", "hearing-impaired",
];

export const ESCALATION_SUGGESTIONS = [
  "I cannot breathe", "I fell", "Chest pain", "I want to hurt myself",
];

export function draftFromAgent(agent: Pick<
  AgentDetail,
  | "agentName"
  | "botName"
  | "dob"
  | "age"
  | "condition"
  | "tags"
  | "escalationPhrases"
  | "topicsToAvoid"
  | "personaOverride"
>): ClientFormDraft {
  return {
    name: agent.agentName ?? "",
    dob: agent.dob ?? null,
    age: typeof agent.age === "number" ? agent.age : null,
    condition: agent.condition ?? "",
    tags: [...(agent.tags ?? [])],
    botName: agent.botName ?? "",
    escalationPhrases: [...(agent.escalationPhrases ?? [])],
    topicsToAvoid: [...(agent.topicsToAvoid ?? [])],
    personaOverride: agent.personaOverride ?? "",
  };
}

export function draftsEqual(a: ClientFormDraft, b: ClientFormDraft): boolean {
  return JSON.stringify(normalizeDraft(a)) === JSON.stringify(normalizeDraft(b));
}

export function normalizeDraft(d: ClientFormDraft): ClientFormDraft {
  return {
    name: d.name.trim(),
    dob: d.dob || null,
    age: d.age ?? null,
    condition: d.condition.trim(),
    tags: d.tags.map((t) => t.trim()).filter(Boolean),
    botName: d.botName.trim(),
    escalationPhrases: d.escalationPhrases.map((t) => t.trim()).filter(Boolean),
    topicsToAvoid: d.topicsToAvoid.map((t) => t.trim()).filter(Boolean),
    personaOverride: d.personaOverride.trim(),
  };
}

export function clientPatchBody(draft: ClientFormDraft): Record<string, unknown> {
  const n = normalizeDraft(draft);
  return {
    name: n.name,
    dob: n.dob,
    age: n.age,
    condition: n.condition || null,
    tags: n.tags,
    botName: n.botName,
    escalationPhrases: n.escalationPhrases,
    topicsToAvoid: n.topicsToAvoid,
    personaOverride: n.personaOverride || null,
  };
}
