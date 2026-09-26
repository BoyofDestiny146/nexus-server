"use client";

import { useEffect, useRef, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Cpu, Loader2, Plus } from "lucide-react";
import { apiGet, apiPatch, apiPut, ApiError } from "@/lib/api";
import type { AgentDetail, DeviceRow, KnowledgeBase, KnowledgeBaseList } from "@/lib/types";
import {
  EDIT_CLIENT_FORM_STEPS,
  clientPatchBody,
  draftFromAgent,
  draftsEqual,
  type ClientFormDraft,
} from "@/lib/clientForm";
import { knowledgeIdsEqual } from "@/lib/knowledge";
import { classNames, relativeTime } from "@/lib/format";
import { Modal } from "@/components/Modal";
import { WizardStepper } from "@/components/Wizard";
import { ProfileFields } from "@/components/clientForm/ProfileFields";
import { GuardrailsFields } from "@/components/clientForm/GuardrailsFields";
import { KnowledgeAccessFields } from "@/components/clientForm/KnowledgeAccessFields";
import { ProfileReview } from "@/components/clientForm/ReviewFields";

const WATCHER_ONLINE_MS = 5 * 60_000;

function isWatcherOnline(lastConnectedAt: string | null | undefined): boolean {
  if (!lastConnectedAt) return false;
  const t = new Date(lastConnectedAt).getTime();
  if (Number.isNaN(t)) return false;
  return Date.now() - t < WATCHER_ONLINE_MS;
}

export function EditClientWorkspace({
  open,
  onClose,
  agent,
  devices,
  onSaved,
  onAttachDevice,
}: {
  open: boolean;
  onClose: () => void;
  agent: AgentDetail;
  devices: DeviceRow[];
  onSaved: () => void;
  onAttachDevice: () => void;
}) {
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<ClientFormDraft>(() => draftFromAgent(agent));
  const [baseline, setBaseline] = useState<ClientFormDraft>(() => draftFromAgent(agent));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<KnowledgeBase[]>([]);
  const [selectedKbIds, setSelectedKbIds] = useState<number[]>([]);
  const [baselineKbIds, setBaselineKbIds] = useState<number[]>([]);
  const [kbLoading, setKbLoading] = useState(false);
  const [kbError, setKbError] = useState<string | null>(null);
  const [kbReady, setKbReady] = useState(false);
  const agentRef = useRef(agent);
  agentRef.current = agent;

  useEffect(() => {
    if (!open) return;
    const next = draftFromAgent(agentRef.current);
    setDraft(next);
    setBaseline(next);
    setStep(0);
    setErr(null);
    setBusy(false);
    setKbReady(false);
    setKbError(null);
    setKbLoading(true);
    const agentId = agentRef.current.id;
    Promise.all([
      apiGet<KnowledgeBaseList>("/knowledge-base"),
      apiGet<KnowledgeBaseList>(`/agent/${agentId}/knowledge-bases`),
    ])
      .then(([all, assigned]) => {
        setCatalog(all.list || []);
        const ids = (assigned.list || [])
          .filter((kb) => kb.assignmentEnabled !== false)
          .map((kb) => kb.id);
        setSelectedKbIds(ids);
        setBaselineKbIds(ids);
        setKbReady(true);
      })
      .catch((e) => {
        setKbError(e instanceof ApiError ? e.message : "Failed to load knowledge access.");
      })
      .finally(() => setKbLoading(false));
  }, [open]);

  function update<K extends keyof ClientFormDraft>(key: K, value: ClientFormDraft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  const profileDirty = !draftsEqual(draft, baseline);
  const knowledgeDirty = kbReady && !knowledgeIdsEqual(selectedKbIds, baselineKbIds);
  const dirty = profileDirty || knowledgeDirty;
  const nameOk = draft.name.trim().length > 0;
  const lastStep = EDIT_CLIENT_FORM_STEPS.length - 1;

  async function save() {
    if (busy || !dirty || !nameOk) return;
    setBusy(true);
    setErr(null);
    try {
      if (profileDirty) {
        await apiPatch(`/agent/${agent.id}`, clientPatchBody(draft));
      }
      if (knowledgeDirty) {
        await apiPut(`/agent/${agent.id}/knowledge-bases`, {
          assignments: selectedKbIds.map((id) => ({ knowledgeBaseId: id, enabled: true })),
        });
      }
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not save client.");
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Edit client"
      size="workspace"
      footer={
        <>
          <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          {step < lastStep ? (
            <button
              type="button"
              className="btn-primary"
              disabled={step === 0 && !nameOk}
              onClick={() => setStep((s) => Math.min(lastStep, s + 1))}
            >
              Next <ChevronRight size={14} />
            </button>
          ) : (
            <button
              type="button"
              className="btn-primary"
              disabled={!dirty || busy || !nameOk}
              onClick={() => void save()}
            >
              {busy && <Loader2 size={14} className="animate-spin" />}
              <Check size={14} /> Save changes
            </button>
          )}
        </>
      }
    >
      <div className="space-y-6">
        <div className="card p-2">
          <WizardStepper
            steps={[...EDIT_CLIENT_FORM_STEPS]}
            current={step}
            onJump={(i) => setStep(i)}
          />
        </div>
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
          <div className="xl:col-span-2 min-h-[18rem]">
            {step === 0 && <ProfileFields draft={draft} update={update} autoFocusName />}
            {step === 1 && (
              <GuardrailsFields draft={draft} update={update} preserveExisting />
            )}
            {step === 2 && (
              <EditDeviceStep
                devices={devices}
                onAttachDevice={() => {
                  if (dirty) {
                    setErr("Save or cancel these edits before attaching a Watcher. Attach uses the existing device control and closes this editor.");
                    return;
                  }
                  onAttachDevice();
                }}
              />
            )}
            {step === 3 && (
              <KnowledgeAccessFields
                catalog={catalog}
                selectedIds={selectedKbIds}
                loading={kbLoading}
                error={kbError}
                onToggle={(id, checked) => {
                  setSelectedKbIds((prev) => (
                    checked ? Array.from(new Set([...prev, id])) : prev.filter((x) => x !== id)
                  ));
                }}
              />
            )}
            {step === 4 && (
              <div className="space-y-6">
                <ProfileReview draft={draft} />
                <div className="border-t border-slate-line/70 pt-6">
                  <div className="kicker mb-3">Device</div>
                  {devices.length === 0 ? (
                    <p className="text-[14px] text-slate-muted">No Watcher bound — attach from the Device step or the client sidebar.</p>
                  ) : (
                    <ul className="space-y-1 text-[14px] text-slate-deep">
                      {devices.map((d) => (
                        <li key={d.id} className="font-mono text-[13px]">
                          {d.alias || d.macAddress}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div className="border-t border-slate-line/70 pt-6">
                  <div className="kicker mb-3">Knowledge Access</div>
                  {selectedKbIds.length === 0 ? (
                    <p className="text-[14px] text-slate-muted">No knowledge bases assigned.</p>
                  ) : (
                    <ul className="space-y-1 text-[14px] text-slate-deep">
                      {catalog.filter((kb) => selectedKbIds.includes(kb.id)).map((kb) => (
                        <li key={kb.id}>{kb.name}</li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            )}
            {err && (
              <div role="alert" className="mt-6 text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
                {err}
              </div>
            )}
            {step > 0 && (
              <div className="mt-6">
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={busy}
                  onClick={() => setStep((s) => Math.max(0, s - 1))}
                >
                  <ChevronLeft size={14} /> Back
                </button>
              </div>
            )}
          </div>
          <aside className="xl:col-span-1">
            <div className="card p-5 bg-bone-soft/70">
              <div className="kicker mb-3">{EDIT_CLIENT_FORM_STEPS[step].label}</div>
              <p className="text-[13.5px] leading-relaxed text-slate-deep">
                {step === 0 && "These are the same profile fields as Add a client. Bot name starts voice display commands."}
                {step === 1 && "Guardrails stay with this client. Existing persona text is kept unless you edit it."}
                {step === 2 && "Device bind and unbind stay on the existing Attach device control. This step shows current assignment only."}
                {step === 3 && "Assign reusable Knowledge Bases. Bound Watchers inherit these. Revel tags are metadata only."}
                {step === 4 && "Save updates this client and knowledge assignments. Integrations, chat history, and assessments are not changed."}
              </p>
            </div>
          </aside>
        </div>
      </div>
    </Modal>
  );
}

function EditDeviceStep({
  devices,
  onAttachDevice,
}: {
  devices: DeviceRow[];
  onAttachDevice: () => void;
}) {
  return (
    <div className="space-y-5">
      {devices.length === 0 ? (
        <p className="text-[14px] text-slate-muted leading-relaxed">
          No Watcher is bound to this client. Conversations will not be recorded here until one is attached.
        </p>
      ) : (
        <ul className="space-y-3">
          {devices.map((d) => {
            const online = isWatcherOnline(d.lastConnectedAt);
            return (
              <li key={d.id} className="card p-4">
                <div className="text-[15px] text-slate-deep font-medium">{d.alias?.trim() || "SenseCAP Watcher"}</div>
                <div className="mt-1 font-mono text-[13px] text-slate-muted">{d.macAddress}</div>
                {d.clientDeviceId ? (
                  <div className="mt-1 text-[12px] text-slate-muted">Device ID {d.clientDeviceId}</div>
                ) : null}
                <div className="mt-2 flex items-center gap-2 text-[12px]">
                  <span className={classNames("w-1.5 h-1.5 rounded-full", online ? "bg-teal" : "bg-slate-line")} />
                  <span className={online ? "text-teal-deep" : "text-slate-muted"}>
                    {online ? "Online" : "Bound"}
                  </span>
                  {!online && d.lastConnectedAt ? (
                    <span className="text-slate-muted">· {relativeTime(d.lastConnectedAt)}</span>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <p className="text-[12.5px] text-slate-muted leading-relaxed">
        Attach or unbind Watchers with the existing Device controls on this client page. This editor does not create a second device lifecycle.
      </p>
      <button type="button" className="btn-secondary text-[13px]" onClick={onAttachDevice}>
        <Plus size={14} /> <Cpu size={14} /> Attach a Watcher
      </button>
    </div>
  );
}
