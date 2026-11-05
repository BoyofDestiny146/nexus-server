"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { Bot } from "lucide-react";
import { apiGet, apiPut, ApiError } from "@/lib/api";
import type { AgentDetail, AgentPersonality, KnowledgeBaseList, Organization } from "@/lib/types";
import { AppShell, PageHeader } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { EmptyState } from "@/components/EmptyState";
import {
  AUTHENTICATION_STATUS,
  INTEGRATION_PRODUCT,
  LAST_VERIFIED_COMMUNICATION,
  PORTAL_AI_AGENTS_ORGANIZATION_ID,
  SAM_PERMISSIONS,
  VIRTUAL_AGENT_STATUS,
  activeIntegrationLabel,
  assignedModelLabel,
  assignedModelNote,
  clientDetailHref,
  inheritedModelLabel,
  knowledgeAssignmentLabel,
  parseVirtualAgentControlId,
  personalitySummary,
  selectVirtualAgents,
  serverDefaultWarning,
  servingModelLabel,
  type ModelCatalogState,
  type AskSamModelStatus,
  type VirtualAgentCandidate,
} from "@/lib/virtualAgents";
import {
  SAM_REVIEW_EMPTY,
  agentSamReviewPath,
  samContextLabel,
  samFeedbackLabel,
  samFollowUpHeading,
  type SamReviewSession,
  type SamReviewSummary,
} from "@/lib/samConversationReview";

interface ListedClient extends VirtualAgentCandidate {
  agentName: string;
  botName?: string | null;
}

interface ControlModel {
  id: string;
  agentName: string;
  clientName: string;
  organizationName: string;
  knowledge: string;
  voicePipeline: string;
  personalityTitle: string;
  personalityDetail: string;
  model: AskSamModelStatus;
}

interface OllamaCatalog {
  serverModel: string;
  triageModel: string;
  embeddingModel: string;
  models: Array<{ name: string; roles: string[] }>;
}

interface ModelProbe {
  model: string;
  ollamaReachable: boolean;
  listed: boolean;
  generationSucceeded: boolean;
  integrationConnected: boolean;
  detail: string;
}

function modelOptionLabel(model: { name: string; roles: string[] }): string {
  const notes = model.roles.filter((role) => role !== "available");
  return notes.length ? `${model.name} (${notes.join(", ")})` : model.name;
}

function Field({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-[14rem_1fr] gap-1 sm:gap-3 py-2.5 border-b border-slate-line/60 last:border-b-0">
      <div className="text-[12px] uppercase tracking-[0.12em] text-slate-muted">{label}</div>
      <div>
        <div className="text-[14px] text-slate-deep">{value}</div>
        {note ? <div className="mt-0.5 text-[12px] text-slate-muted">{note}</div> : null}
      </div>
    </div>
  );
}

function Section({ kicker, children }: { kicker: string; children: ReactNode }) {
  return (
    <section className="card p-6 mb-4">
      <div className="kicker mb-3">{kicker}</div>
      {children}
    </section>
  );
}

function ConversationReview({ agentId }: { agentId: string }) {
  const [summary, setSummary] = useState<SamReviewSummary | null>(null);
  const [sessions, setSessions] = useState<SamReviewSession[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiGet<{ summary: SamReviewSummary; sessions: SamReviewSession[] }>(agentSamReviewPath(agentId))
      .then((review) => {
        if (cancelled) return;
        setSummary(review.summary);
        setSessions(review.sessions);
        setError(null);
      })
      .catch((caught) => {
        if (!cancelled) setError(caught instanceof ApiError ? caught.message : "Conversation review could not be loaded.");
      });
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  return (
    <Section kicker="Conversation review">
      {error && <p className="text-[14px] text-slate-deep">{error}</p>}
      {!error && summary && (
        <div className="mb-4 text-[14px] text-slate-deep">
          <div>Helpful votes: {summary.helpful}</div>
          <div>Not helpful votes: {summary.notHelpful}</div>
          {summary.percent !== null && <div>{summary.percent}% helpful</div>}
        </div>
      )}
      {!error && sessions && sessions.length === 0 && (
        <p className="text-[14px] text-slate-deep">{SAM_REVIEW_EMPTY}</p>
      )}
      {!error && sessions && sessions.map((session) => (
        <article key={session.sessionId} className="mb-4 border-t border-slate-line/60 pt-3">
          <div className="text-[13px] text-slate-muted">{session.createdAt || "Time not recorded"}</div>
          <div className="text-[14px] text-slate-deep">{samContextLabel(session.contextKind)}</div>
          {session.contextKind !== "library" && (
            <div className="text-[13px] text-slate-muted">
              Account {session.accountId || "—"} · Device {session.registeredDeviceId || "—"}
            </div>
          )}
          <div className="text-[13px] text-slate-muted">
            {session.answerCount} completed {session.answerCount === 1 ? "answer" : "answers"} · Feedback {samFeedbackLabel(session.helpful)}
          </div>
          <ol className="mt-2 space-y-2">
            {session.turns.map((turn, index) => (
              <li key={`${session.sessionId}-${index}`} className="text-[14px] text-slate-deep">
                <div className="text-[12px] text-slate-muted">Question</div>
                <div>{turn.question}</div>
                <div className="mt-1 text-[12px] text-slate-muted">Sam</div>
                <div>{turn.answer}</div>
                {turn.followUp ? (
                  <div data-testid="sam-turn-follow-up">
                    <div className="mt-1 text-[12px] text-slate-muted">{samFollowUpHeading("Sam")}</div>
                    <div>{turn.followUp}</div>
                  </div>
                ) : null}
              </li>
            ))}
          </ol>
        </article>
      ))}
    </Section>
  );
}

function ModelAssignment({
  agentId,
  model,
  onSaved,
}: {
  agentId: string;
  model: AskSamModelStatus;
  onSaved: (next: AskSamModelStatus) => void;
}) {
  const [catalog, setCatalog] = useState<OllamaCatalog | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [selected, setSelected] = useState(model.storedModel || "");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [probe, setProbe] = useState<ModelProbe | null>(null);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);

  useEffect(() => {
    setSelected(model.storedModel || "");
  }, [model.storedModel]);

  useEffect(() => {
    let cancelled = false;
    apiGet<OllamaCatalog>("/models/ollama")
      .then((loaded) => {
        if (cancelled) return;
        setCatalog(loaded);
        setCatalogError(null);
      })
      .catch((caught) => {
        if (!cancelled) setCatalogError(caught instanceof ApiError ? caught.message : "Ollama models could not be loaded.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const tags = catalog?.models ?? [];
  const known = new Set(tags.map((item) => item.name));
  const catalogState: ModelCatalogState = catalogError
    ? "unavailable"
    : catalog
      ? tags.map((item) => item.name)
      : "loading";
  const defaultWarning = serverDefaultWarning(model.serverModel, catalogState);

  async function save() {
    setSaving(true);
    setSaveError(null);
    try {
      const next = await apiPut<AskSamModelStatus>(`/agent/${agentId}/ask-sam-model`, {
        askSamModel: selected.trim() ? selected : null,
      });
      onSaved(next);
    } catch (caught) {
      setSaveError(caught instanceof ApiError ? caught.message : "The model could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function verify() {
    const target = selected.trim() || (model.serverModel || "").trim();
    if (!target) {
      setProbeError("The server default is not configured. No other model was selected.");
      setProbe(null);
      return;
    }
    setProbing(true);
    setProbeError(null);
    try {
      const result = await apiGet<ModelProbe>(`/models/ollama/verify?model=${encodeURIComponent(target)}`);
      setProbe(result);
    } catch (caught) {
      setProbe(null);
      setProbeError(caught instanceof ApiError ? caught.message : "The model could not be checked.");
    } finally {
      setProbing(false);
    }
  }

  return (
    <div className="mb-4">
      <Field label="Assigned model" value={assignedModelLabel(model.storedModel)} note={assignedModelNote(model.storedModel)} />
      <Field label="Inherited model" value={inheritedModelLabel(model.serverModel)} note="The current server default. No other installed model is substituted." />
      {defaultWarning ? <p className="mt-2 text-[13px] text-risk-urgent">{defaultWarning}</p> : null}
      <Field
        label="Serving model"
        value={servingModelLabel(model.effectiveModel)}
        note={model.isActiveIntegrationAgent
          ? (model.storedModel
            ? "Ask Sam answers with the model saved on this agent."
            : "Ask Sam answers with the server default. No override is saved on this agent.")
          : "This agent is not serving BridgingCare conversations."}
      />
      <label className="block mt-4 text-[12px] uppercase tracking-[0.12em] text-slate-muted" htmlFor="ask-sam-model">
        Assigned model
      </label>
      <select
        id="ask-sam-model"
        className="input mt-1.5 w-full"
        value={selected}
        onChange={(event) => setSelected(event.target.value)}
      >
        <option value="">Server default (no override)</option>
        {selected && !known.has(selected) ? <option value={selected}>{selected}</option> : null}
        {tags.map((item) => (
          <option key={item.name} value={item.name}>{modelOptionLabel(item)}</option>
        ))}
      </select>
      {catalogError && <p className="mt-2 text-[13px] text-risk-urgent">{catalogError}</p>}
      {saveError && <p className="mt-2 text-[13px] text-risk-urgent">{saveError}</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" className="btn-primary" disabled={saving} onClick={() => void save()}>
          {saving ? "Saving…" : "Save model"}
        </button>
        <button type="button" className="btn-secondary" disabled={probing} onClick={() => void verify()}>
          {probing ? "Checking…" : "Check model availability"}
        </button>
      </div>
      <div className="mt-4">
        <Field label="Model availability" value={probe ? probe.detail : "Not checked"} note="Ollama reachability, tag listing, and a short generation. This is not the integration connection." />
        {probeError && <p className="mt-2 text-[13px] text-risk-urgent">{probeError}</p>}
        {probe && (
          <p className="mt-2 text-[12px] text-slate-muted">
            Reachable {probe.ollamaReachable ? "yes" : "no"} · Listed {probe.listed ? "yes" : "no"} · Generation {probe.generationSucceeded ? "yes" : "no"} · Integration {probe.integrationConnected ? "connected" : "not connected"}
          </p>
        )}
      </div>
    </div>
  );
}

function ControlView() {
  const [model, setModel] = useState<ControlModel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const id = parseVirtualAgentControlId(window.location.search);
    if (!id) {
      setMissing(true);
      return;
    }
    (async () => {
      try {
        const organizationId = PORTAL_AI_AGENTS_ORGANIZATION_ID;
        const [organization, listed] = await Promise.all([
          apiGet<Organization>(`/organizations/${organizationId}`),
          apiGet<ListedClient[]>(`/agent/list?organizationId=${encodeURIComponent(organizationId)}`),
        ]);
        const agent = selectVirtualAgents(listed).find((item) => item.id === id);
        if (!agent) {
          if (!cancelled) setMissing(true);
          return;
        }
        const [detail, knowledge] = await Promise.all([
          apiGet<AgentDetail>(`/agent/${agent.id}`),
          apiGet<KnowledgeBaseList>(`/agent/${agent.id}/knowledge-bases`),
        ]);
        let personalityName: string | null = null;
        if (detail.personalityId) {
          try {
            const catalog = await apiGet<{ personalities: AgentPersonality[] }>("/personalities?includeInactive=true");
            personalityName = catalog.personalities.find((item) => item.id === detail.personalityId)?.name ?? null;
          } catch {
            personalityName = null;
          }
        }
        const personality = personalitySummary({
          personalityId: detail.personalityId,
          personalityName,
          personaOverride: detail.personaOverride,
        });
        if (!cancelled) {
          setModel({
            id: agent.id,
            agentName: (detail.botName || agent.botName || detail.agentName || "Unnamed agent").trim(),
            clientName: detail.agentName || agent.agentName || "Unnamed client",
            organizationName: organization.name,
            knowledge: knowledgeAssignmentLabel(knowledge.list ?? []),
            voicePipeline: detail.llmModelId || "Not returned by the client record",
            personalityTitle: personality.title,
            personalityDetail: personality.detail,
            model: {
              storedModel: detail.storedModel ?? null,
              serverModel: detail.serverModel ?? null,
              effectiveModel: detail.effectiveModel ?? null,
              isActiveIntegrationAgent: detail.isActiveIntegrationAgent === true,
            },
          });
          setError(null);
        }
      } catch (caught) {
        if (!cancelled) setError(caught instanceof ApiError ? caught.message : "Failed to load this virtual agent.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const activeIntegration = model?.model.isActiveIntegrationAgent === true;

  return (
    <>
      <PageHeader
        kicker="Virtual agent"
        title={model?.agentName || "Control"}
        subtitle="Informational control panel. It does not change Watcher voice, alarm rules, or the server allowlist."
        actions={
          <Link href="/virtual-agents" className="btn-ghost text-[12px]">
            ← Virtual Agents
          </Link>
        }
      />
      <section className="px-8 md:px-12 py-8">
        {error && (
          <div className="card p-6 border-risk-urgent/30 bg-risk-urgent/5 text-risk-urgent text-[14px] mb-4">
            {error}
          </div>
        )}
        {missing && !error && (
          <EmptyState
            icon={Bot}
            title="No virtual agent selected"
            body="Open Control from a virtual agent row. Physical agents stay on the Agents page."
          />
        )}
        {model === null && !missing && !error && <div className="card p-8 skeleton h-72" />}
        {model && (
          <>
            <Section kicker="Agent information">
              <Field label="Agent name" value={model.agentName} />
              <Field label="Organization" value={model.organizationName} />
              <Field
                label="Client"
                value={model.clientName}
                note={model.id}
              />
              <div className="pt-2">
                <Link href={clientDetailHref(model.id)} className="text-[13px] text-teal-deep hover:underline">
                  Open client page
                </Link>
              </div>
              <Field
                label="Active integration"
                value={activeIntegrationLabel(model.model.isActiveIntegrationAgent)}
                note={model.model.isActiveIntegrationAgent
                  ? "This agent is the BridgingCare integration agent selected by the server."
                  : "This agent is not the BridgingCare integration agent."}
              />
              <Field
                label="Assigned model"
                value={assignedModelLabel(model.model.storedModel)}
                note={assignedModelNote(model.model.storedModel)}
              />
              <Field
                label="Inherited model"
                value={inheritedModelLabel(model.model.serverModel)}
                note="The current server default. No other installed model is substituted."
              />
              <Field label="Connection status" value={VIRTUAL_AGENT_STATUS} note="Integration connectivity. A model probe does not change this." />
            </Section>

            <Section kicker="Knowledge">
              <Field label="Assignment" value={model.knowledge} />
              <p className="mt-3 text-[13px] text-slate-muted leading-relaxed">
                Assignments are edited on the existing client page. This panel does not create a second assignment system, and it does not change the server allowlist.
              </p>
              <div className="mt-3">
                <Link href={clientDetailHref(model.id)} className="text-[13px] text-teal-deep hover:underline">
                  Open knowledge management
                </Link>
              </div>
            </Section>

            <Section kicker="Personality">
              <Field label="Client personality" value={model.personalityTitle} note={model.personalityDetail} />
              <p className="mt-3 text-[13px] text-slate-muted leading-relaxed">
                Informational only. Ask Sam answers from the portal stay read-only. Watcher voice, speed, and volume are not changed here.
              </p>
            </Section>

            <Section kicker="AI model">
              <ModelAssignment
                agentId={model.id}
                model={model.model}
                onSaved={(next) => setModel({ ...model, model: next })}
              />
              <Field
                label="Voice-pipeline slot"
                value={model.voicePipeline}
                note="Client model slot. Not the Ask Sam server model, and not editable here."
              />
            </Section>

            <Section kicker="Integration status">
              <Field label="Integration" value={activeIntegration ? INTEGRATION_PRODUCT : "Not configured"} />
              <Field label="Active integration" value={activeIntegrationLabel(model.model.isActiveIntegrationAgent)} />
              <Field label="Connection status" value={VIRTUAL_AGENT_STATUS} />
              <Field label="Last verified communication" value={LAST_VERIFIED_COMMUNICATION} />
              <Field label="Authentication status" value={AUTHENTICATION_STATUS} />
            </Section>

            {model && <ConversationReview agentId={model.id} />}

            <Section kicker="Permissions">
              {activeIntegration ? (
                <ul className="space-y-2">
                  {SAM_PERMISSIONS.map((item) => (
                    <li key={item} className="text-[14px] text-slate-deep">{item}</li>
                  ))}
                </ul>
              ) : (
                <p className="text-[14px] text-slate-muted">No BridgingCare integration restrictions are registered for this client.</p>
              )}
            </Section>
          </>
        )}
      </section>
    </>
  );
}

export default function VirtualAgentControlPage() {
  return (
    <RequireAuth>
      <AppShell>
        <ControlView />
      </AppShell>
    </RequireAuth>
  );
}
