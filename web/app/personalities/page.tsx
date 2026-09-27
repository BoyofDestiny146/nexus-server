"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Copy, Loader2, Pencil, Sparkles, Trash2 } from "lucide-react";
import { apiDelete, apiGet, apiPost, apiPut, ApiError } from "@/lib/api";
import type { AgentPersonality } from "@/lib/types";
import { PERSONALITY_SECTION_ORDER } from "@/lib/types";
import { AppShell, PageHeader } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { Modal } from "@/components/Modal";
import { ToastProvider, useToast } from "@/components/Toast";
import { classNames } from "@/lib/format";

function PersonalitiesView() {
  const toast = useToast();
  const [rows, setRows] = useState<AgentPersonality[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [edit, setEdit] = useState<AgentPersonality | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftDescription, setDraftDescription] = useState("");
  const [draftPrompt, setDraftPrompt] = useState("");
  const [saveBusy, setSaveBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const data = await apiGet<{ personalities: AgentPersonality[] }>("/personalities?includeInactive=true");
      setRows(data.personalities || []);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load personalities.");
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  const grouped = useMemo(() => {
    const map = new Map<string, AgentPersonality[]>();
    for (const section of PERSONALITY_SECTION_ORDER) map.set(section.key, []);
    for (const row of rows || []) {
      const key = map.has(row.category) ? row.category : "custom";
      map.get(key)!.push(row);
    }
    return map;
  }, [rows]);

  async function duplicate(row: AgentPersonality) {
    setBusyId(row.id);
    try {
      await apiPost(`/personalities/${row.id}/duplicate`, { name: `Copy of ${row.name}` });
      toast.push(`Duplicated ${row.name}.`, "success");
      await reload();
    } catch (e) {
      toast.push(e instanceof ApiError ? e.message : "Could not duplicate.", "error");
    } finally {
      setBusyId(null);
    }
  }

  function openEdit(row: AgentPersonality) {
    setEdit(row);
    setDraftName(row.name);
    setDraftDescription(row.description || "");
    setDraftPrompt(row.promptTemplate || "");
  }

  async function saveEdit() {
    if (!edit || saveBusy) return;
    setSaveBusy(true);
    try {
      await apiPut(`/personalities/${edit.id}`, {
        name: draftName,
        description: draftDescription,
        promptTemplate: draftPrompt,
      });
      toast.push("Personality saved.", "success");
      setEdit(null);
      await reload();
    } catch (e) {
      toast.push(e instanceof ApiError ? e.message : "Could not save.", "error");
    } finally {
      setSaveBusy(false);
    }
  }

  async function deactivate(row: AgentPersonality) {
    setBusyId(row.id);
    try {
      await apiPost(`/personalities/${row.id}/deactivate`);
      toast.push(`${row.name} deactivated.`, "success");
      await reload();
    } catch (e) {
      toast.push(e instanceof ApiError ? e.message : "Could not deactivate.", "error");
    } finally {
      setBusyId(null);
    }
  }

  async function remove(row: AgentPersonality) {
    setBusyId(row.id);
    try {
      await apiDelete(`/personalities/${row.id}`);
      toast.push(`${row.name} deleted.`, "success");
      await reload();
    } catch (e) {
      toast.push(e instanceof ApiError ? e.message : "Could not delete. Deactivate if it is in use.", "error");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      <PageHeader
        kicker="Configuration"
        title="Agent Personality"
        subtitle="How Nexus talks. Built-in personalities are read-only; duplicate one to make an editable custom copy. This is independent of Assessment Profile and Knowledge Bases."
      />
      <section className="px-8 md:px-12 py-8 space-y-8">
        {error && (
          <div className="card p-6 border-risk-urgent/30 bg-risk-urgent/5 text-risk-urgent text-[14px]">
            {error}
          </div>
        )}
        {!rows && !error && <div className="card p-8 skeleton h-48" />}
        {PERSONALITY_SECTION_ORDER.map((section) => {
          const list = grouped.get(section.key) || [];
          if (list.length === 0 && section.key !== "custom") return null;
          return (
            <div key={section.key}>
              <div className="kicker mb-3">{section.label}</div>
              {list.length === 0 ? (
                <p className="text-[14px] text-slate-muted">No custom personalities yet. Duplicate a system personality to start.</p>
              ) : (
                <ul className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {list.map((row) => (
                    <li key={row.id} className="card p-5">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <h2 className="display-3 text-slate-deep">{row.name}</h2>
                          <p className="mt-1 text-[13px] text-slate-muted leading-relaxed">
                            {row.description || "—"}
                          </p>
                          <div className="mt-2 text-[11px] uppercase tracking-[0.12em] text-slate-muted">
                            {row.category}
                            {row.isSystem ? " · read-only" : ""}
                            {!row.isActive ? " · inactive" : ""}
                          </div>
                        </div>
                        <Sparkles size={16} className="text-teal shrink-0 mt-1" />
                      </div>
                      <div className="mt-4 flex flex-wrap gap-2">
                        <button
                          type="button"
                          className="btn-secondary text-[12px]"
                          disabled={busyId === row.id}
                          onClick={() => void duplicate(row)}
                        >
                          {busyId === row.id ? <Loader2 size={12} className="animate-spin" /> : <Copy size={12} />}
                          Create Duplicate
                        </button>
                        {!row.isSystem && (
                          <>
                            <button type="button" className="btn-ghost text-[12px]" onClick={() => openEdit(row)}>
                              <Pencil size={12} /> Edit
                            </button>
                            {row.isActive ? (
                              <button type="button" className="btn-ghost text-[12px]" onClick={() => void deactivate(row)}>
                                Deactivate
                              </button>
                            ) : null}
                            <button type="button" className="btn-ghost text-[12px] text-risk-urgent" onClick={() => void remove(row)}>
                              <Trash2 size={12} /> Delete
                            </button>
                          </>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </section>

      <Modal
        open={!!edit}
        onClose={() => !saveBusy && setEdit(null)}
        title="Edit personality"
        size="workspace"
        footer={
          <>
            <button type="button" className="btn-secondary" disabled={saveBusy} onClick={() => setEdit(null)}>Cancel</button>
            <button type="button" className="btn-primary" disabled={saveBusy || !draftName.trim() || !draftPrompt.trim()} onClick={() => void saveEdit()}>
              {saveBusy && <Loader2 size={14} className="animate-spin" />}
              Save
            </button>
          </>
        }
      >
        <div className="space-y-5">
          <div>
            <label className="label" htmlFor="p-name">Name</label>
            <input id="p-name" className="input" value={draftName} onChange={(e) => setDraftName(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="p-desc">Description</label>
            <input id="p-desc" className="input" value={draftDescription} onChange={(e) => setDraftDescription(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="p-prompt">Prompt template</label>
            <textarea
              id="p-prompt"
              rows={12}
              className="input font-mono text-[13px]"
              value={draftPrompt}
              onChange={(e) => setDraftPrompt(e.target.value)}
            />
            <div className="helper">
              Use {"{{assistant_name}}"} for the bound companion name. Nexus safety rules are applied automatically and cannot be removed here.
            </div>
          </div>
        </div>
      </Modal>
    </>
  );
}

export default function PersonalitiesPage() {
  return (
    <RequireAuth>
      <ToastProvider>
        <AppShell>
          <PersonalitiesView />
        </AppShell>
      </ToastProvider>
    </RequireAuth>
  );
}
