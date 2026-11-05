"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiGet, apiPost, apiPut, ApiError } from "@/lib/api";
import type { AgentSummary, KnowledgeBase, KnowledgeBaseList } from "@/lib/types";
import { AppShell, PageHeader } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { KnowledgeAccessFields } from "@/components/clientForm/KnowledgeAccessFields";
import {
  knowledgeAssignmentPutBody,
  toggleKnowledgeSelection,
} from "@/lib/knowledge";
import {
  bindVirtualAgentPayload,
  clientVirtualAgentsPath,
} from "@/lib/virtualAgentBinding";
import {
  createVirtualAgentPayload,
  virtualAgentControlHref,
  virtualAgentCreatePath,
  visibleClientRecords,
} from "@/lib/virtualAgents";

interface OllamaCatalog {
  models: Array<{ name: string; roles: string[] }>;
}

interface CreatedVirtualAgent {
  id: string;
  agentName: string;
}

function modelOptionLabel(model: { name: string; roles: string[] }): string {
  const notes = model.roles.filter((role) => role !== "available");
  return notes.length ? `${model.name} (${notes.join(", ")})` : model.name;
}

function CreateVirtualAgentForm() {
  const [name, setName] = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const [catalog, setCatalog] = useState<OllamaCatalog | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [kbCatalog, setKbCatalog] = useState<KnowledgeBase[]>([]);
  const [selectedKbIds, setSelectedKbIds] = useState<number[]>([]);
  const [kbLoading, setKbLoading] = useState(true);
  const [kbError, setKbError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<CreatedVirtualAgent | null>(null);

  const [attachClients, setAttachClients] = useState<AgentSummary[] | null>(null);
  const [attachClientId, setAttachClientId] = useState("");
  const [attachBusy, setAttachBusy] = useState(false);
  const [attachError, setAttachError] = useState<string | null>(null);
  const [attachedClientId, setAttachedClientId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiGet<OllamaCatalog>("/models/ollama")
      .then((loaded) => {
        if (cancelled) return;
        setCatalog(loaded);
        setCatalogError(null);
      })
      .catch((caught) => {
        if (!cancelled) {
          setCatalog(null);
          setCatalogError(caught instanceof ApiError ? caught.message : "Ollama models could not be loaded.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setKbLoading(true);
    apiGet<KnowledgeBaseList>("/knowledge-base")
      .then((loaded) => {
        if (cancelled) return;
        setKbCatalog(loaded.list || []);
        setKbError(null);
      })
      .catch((caught) => {
        if (!cancelled) {
          setKbCatalog([]);
          setKbError(caught instanceof ApiError ? caught.message : "Knowledge bases could not be loaded.");
        }
      })
      .finally(() => {
        if (!cancelled) setKbLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!created) return;
    let cancelled = false;
    apiGet<AgentSummary[]>("/agent/list")
      .then((listed) => {
        if (cancelled) return;
        setAttachClients(visibleClientRecords(listed));
      })
      .catch(() => {
        if (!cancelled) setAttachClients([]);
      });
    return () => {
      cancelled = true;
    };
  }, [created]);

  const nameOk = name.trim().length > 0;
  const tags = catalog?.models ?? [];

  async function save() {
    if (!nameOk || busy) return;
    setBusy(true);
    setError(null);
    try {
      const createdRow = await apiPost<{
        id?: string;
        agentId?: string;
        agentName?: string;
        botName?: string;
      }>(virtualAgentCreatePath(), createVirtualAgentPayload(name));
      const id = (createdRow.id || createdRow.agentId || "").trim();
      if (!id) throw new Error("Create did not return an agent id.");
      const displayName = (createdRow.botName || createdRow.agentName || name).trim() || name.trim();

      if (selectedModel.trim()) {
        await apiPut(`/agent/${id}/ask-sam-model`, { askSamModel: selectedModel.trim() });
      }
      if (selectedKbIds.length > 0) {
        await apiPut(`/agent/${id}/knowledge-bases`, knowledgeAssignmentPutBody(selectedKbIds));
      }

      setCreated({ id, agentName: displayName });
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The virtual agent could not be created.");
    } finally {
      setBusy(false);
    }
  }

  async function attachToClient() {
    if (!created || !attachClientId || attachBusy || attachedClientId) return;
    setAttachBusy(true);
    setAttachError(null);
    try {
      await apiPost(clientVirtualAgentsPath(attachClientId), bindVirtualAgentPayload(created.id));
      setAttachedClientId(attachClientId);
    } catch (caught) {
      setAttachError(caught instanceof ApiError ? caught.message : "Could not attach this virtual agent.");
    } finally {
      setAttachBusy(false);
    }
  }

  if (created) {
    const attachedName = attachClients?.find((row) => row.id === attachedClientId)?.agentName;
    return (
      <>
        <PageHeader
          kicker="Virtual agent"
          title="Virtual agent created"
          subtitle="Saved unbound. Attach to a care client only when you choose to."
          actions={
            <Link href="/virtual-agents" className="btn-ghost text-[12px]">
              ← Virtual Agents
            </Link>
          }
        />
        <section className="px-8 md:px-12 py-8 max-w-2xl">
          <div className="card p-6 space-y-4">
            <div>
              <div className="text-[12px] uppercase tracking-[0.12em] text-slate-muted">Agent name</div>
              <div className="mt-1 text-[16px] text-slate-deep">{created.agentName}</div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Link href="/virtual-agents" className="btn-secondary text-[12px]">
                Back to list
              </Link>
              <Link href={virtualAgentControlHref(created.id)} className="btn-primary text-[12px]">
                Open Control
              </Link>
            </div>
          </div>

          <div className="card p-6 mt-4 space-y-4">
            <div className="kicker">Attach to client (optional)</div>
            <p className="text-[14px] text-slate-muted leading-relaxed">
              Binding is optional. Saving never auto-attaches. Use the existing virtual-agent bind API when you are ready.
            </p>
            {attachedClientId ? (
              <p className="text-[14px] text-slate-deep">
                Attached to {attachedName || attachedClientId}.
              </p>
            ) : (
              <>
                <label htmlFor="attach-care-client" className="label">Care client</label>
                <select
                  id="attach-care-client"
                  className="input"
                  value={attachClientId}
                  onChange={(event) => setAttachClientId(event.target.value)}
                  disabled={attachClients === null}
                >
                  <option value="">Select a care client</option>
                  {(attachClients ?? []).map((client) => (
                    <option key={client.id} value={client.id}>
                      {client.agentName || client.id}
                    </option>
                  ))}
                </select>
                {attachClients && attachClients.length === 0 && (
                  <p className="text-[13px] text-slate-muted">No care clients are available to attach.</p>
                )}
                {attachError && (
                  <div className="text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
                    {attachError}
                  </div>
                )}
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={!attachClientId || attachBusy || attachClients === null}
                  onClick={() => void attachToClient()}
                >
                  {attachBusy ? "Attaching…" : "Attach to Client"}
                </button>
              </>
            )}
          </div>
        </section>
      </>
    );
  }

  return (
    <>
      <PageHeader
        kicker="Virtual agent"
        title="Add Virtual Agent"
        subtitle="Creates an unbound software agent in Portal AI Agents. Model and knowledge use the same endpoints as Control. Binding is optional after save."
        actions={
          <Link href="/virtual-agents" className="btn-ghost text-[12px]">
            ← Virtual Agents
          </Link>
        }
      />
      <section className="px-8 md:px-12 py-8 max-w-2xl">
        <div className="card p-6 space-y-6">
          <div>
            <label htmlFor="va-name" className="label">Agent name</label>
            <input
              id="va-name"
              className="input mt-1.5"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={64}
              placeholder="Display name"
              autoComplete="off"
            />
          </div>

          <div>
            <label htmlFor="va-model" className="label">Assigned model</label>
            <select
              id="va-model"
              className="input mt-1.5"
              value={selectedModel}
              onChange={(event) => setSelectedModel(event.target.value)}
            >
              <option value="">Server default (no override)</option>
              {selectedModel && !tags.some((item) => item.name === selectedModel) ? (
                <option value={selectedModel}>{selectedModel}</option>
              ) : null}
              {tags.map((item) => (
                <option key={item.name} value={item.name}>{modelOptionLabel(item)}</option>
              ))}
            </select>
            <p className="mt-1.5 text-[12px] text-slate-muted">
              Optional. Saved with the existing Ask Sam model endpoint after create. Leave blank to inherit the server default.
            </p>
            {catalogError && <p className="mt-2 text-[13px] text-risk-urgent">{catalogError}</p>}
          </div>

          <div>
            <div className="label mb-2">Knowledge</div>
            <KnowledgeAccessFields
              catalog={kbCatalog}
              selectedIds={selectedKbIds}
              onToggle={(id, checked) => setSelectedKbIds((ids) => toggleKnowledgeSelection(ids, id, checked))}
              loading={kbLoading}
              error={kbError}
            />
            <p className="mt-2 text-[12px] text-slate-muted">
              Optional. Assigned with the existing client knowledge endpoint after create.
            </p>
          </div>

          {error && (
            <div className="text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
              {error}
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-primary" disabled={!nameOk || busy} onClick={() => void save()}>
              {busy ? "Saving…" : "Save Virtual Agent"}
            </button>
            <Link href="/virtual-agents" className="btn-secondary">
              Cancel
            </Link>
          </div>
        </div>
      </section>
    </>
  );
}

export default function AddVirtualAgentPage() {
  return (
    <RequireAuth>
      <AppShell>
        <CreateVirtualAgentForm />
      </AppShell>
    </RequireAuth>
  );
}
