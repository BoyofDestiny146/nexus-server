"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  ChevronLeft, ChevronRight, Check, Loader2, RefreshCw,
  Info, Copy,
} from "lucide-react";
import { ApiError, apiGet, apiPost } from "@/lib/api";
import type { OnboardRequest, UnboundDevice } from "@/lib/types";
import { ageFromDob, classNames, relativeTime } from "@/lib/format";
import { deviceSetupUrl } from "@/lib/serverConfig";
import { AppShell } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { WizardStepper } from "@/components/Wizard";
import { ToastProvider, useToast } from "@/components/Toast";
import { ProfileFields } from "@/components/clientForm/ProfileFields";
import { GuardrailsFields } from "@/components/clientForm/GuardrailsFields";
import { ProfileReview, ReviewRow } from "@/components/clientForm/ReviewFields";
import {
  CLIENT_FORM_STEPS,
  EMPTY_CLIENT_DRAFT,
  type ClientFormDraft,
} from "@/lib/clientForm";

interface DraftState extends ClientFormDraft {
  eui: string | null;
  deviceAlias: string;
  clientDeviceId: string;
  deviceType: OnboardRequest["deviceType"];
  firmwareType: OnboardRequest["firmwareType"];
  deviceMode: "auto" | "paste" | "skip";
}

const EMPTY_DRAFT: DraftState = {
  ...EMPTY_CLIENT_DRAFT,
  eui: null,
  deviceAlias: "",
  clientDeviceId: "",
  deviceType: "W1-A",
  firmwareType: "xiaozhi",
  deviceMode: "auto",
};

const DRAFT_KEY = "careconnect.onboard.draft";

function normalizeEui(raw: string): string {
  return raw.replace(/[\s:\-]/g, "").toUpperCase();
}
function isValidEui(raw: string): boolean {
  const n = normalizeEui(raw);
  return /^[0-9A-F]{12}$|^[0-9A-F]{16}$/.test(n);
}

function OnboardWizard() {
  const router = useRouter();
  const toast = useToast();

  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<DraftState>(EMPTY_DRAFT);
  const [resumeAvailable, setResumeAvailable] = useState(false);
  const [submitBusy, setSubmitBusy] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const [unbound, setUnbound] = useState<UnboundDevice[] | null>(null);
  const [unboundBusy, setUnboundBusy] = useState(false);
  const [pickedEui, setPickedEui] = useState<string | null>(null);
  const [eUiText, setEuiText] = useState("");
  const [euiError, setEuiError] = useState<string | null>(null);

  // restore draft (if any) on mount
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(DRAFT_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as DraftState;
        if (parsed && (parsed.name || parsed.condition)) {
          setResumeAvailable(true);
        }
      }
    } catch { /* ignore */ }
  }, []);

  // persist draft on every change
  useEffect(() => {
    if (draft === EMPTY_DRAFT) return;
    try {
      window.localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
    } catch { /* ignore quota */ }
  }, [draft]);

  // load unbound devices when entering step 2 in auto mode
  useEffect(() => {
    if (step !== 2 || draft.deviceMode !== "auto") return;
    refreshUnbound();
  }, [step, draft.deviceMode]);

  async function refreshUnbound() {
    setUnboundBusy(true);
    try {
      const data = await apiGet<UnboundDevice[]>("/device/unbound-recent?windowMinutes=10");
      setUnbound(data ?? []);
    } catch {
      setUnbound([]);
    } finally {
      setUnboundBusy(false);
    }
  }

  function resumeDraft() {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(DRAFT_KEY) || "");
      setDraft({ ...EMPTY_DRAFT, ...parsed });
      setResumeAvailable(false);
    } catch {
      setResumeAvailable(false);
    }
  }
  function discardDraft() {
    window.localStorage.removeItem(DRAFT_KEY);
    setResumeAvailable(false);
  }

  function update<K extends keyof DraftState>(key: K, value: DraftState[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }
  function updateForm<K extends keyof ClientFormDraft>(key: K, value: ClientFormDraft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  // Per-step validation
  const stepValid = useMemo(() => {
    if (step === 0) return draft.name.trim().length > 0;
    if (step === 2) {
      if (draft.deviceMode === "auto") return !!pickedEui;
      if (draft.deviceMode === "paste") return !!eUiText && isValidEui(eUiText);
      return true; // skip
    }
    return true;
  }, [step, draft, pickedEui, eUiText]);

  async function submit() {
    if (submitBusy) return;
    setSubmitError(null);
    setSubmitBusy(true);
    const eui = draft.deviceMode === "auto" ? pickedEui :
                draft.deviceMode === "paste" ? normalizeEui(eUiText) : null;
    const payload: OnboardRequest = {
      name: draft.name.trim(),
      dob: draft.dob || null,
      age: draft.age ?? ageFromDob(draft.dob),
      condition: draft.condition?.trim() || null,
      tags: draft.tags ?? [],
      escalationPhrases: draft.escalationPhrases ?? [],
      topicsToAvoid: draft.topicsToAvoid ?? [],
      personaOverride: draft.personaOverride?.trim() || null,
      botName: draft.botName?.trim() || null,
      eui,
      deviceAlias: eui ? (draft.deviceAlias?.trim() || `${draft.name.trim()}'s Watcher`) : null,
      clientDeviceId: eui ? (draft.clientDeviceId?.trim() || null) : null,
      deviceType: eui ? "W1-A" : null,
      firmwareType: eui ? "xiaozhi" : null,
    };
    try {
      const res = await apiPost<{ agentId: string; deviceId?: string }>("/agent/onboard", payload);
      window.localStorage.removeItem(DRAFT_KEY);
      toast.push(`${draft.name.trim()} added to your roster.`, "success");
      router.push(`/patients/${res.agentId}`);
    } catch (e) {
      setSubmitError(e instanceof ApiError ? e.message : "Could not create client.");
      setSubmitBusy(false);
    }
  }

  return (
    <>
      <header className="px-8 md:px-12 pt-8 pb-6 border-b border-slate-line/70">
        <Link href="/patients" className="inline-flex items-center gap-1.5 text-[12px] uppercase tracking-[0.12em] text-slate-muted hover:text-slate-deep transition mb-3">
          <ChevronLeft size={14} /> Roster
        </Link>
        <h1 className="display-1 text-slate-deep">Add a client</h1>
        <p className="mt-2 text-[14px] text-slate-muted max-w-2xl leading-relaxed">
          A client is a profile + guard rules + an optional Watcher binding.
          You can change anything later.
        </p>
      </header>

      {resumeAvailable && (
        <div className="mx-8 md:mx-12 mt-6 card p-4 flex items-center justify-between gap-4 bg-teal-tint/40 border-teal/30">
          <div className="flex items-center gap-3 text-[13px] text-teal-deep">
            <Info size={16} />
            <span>You have an unfinished onboarding draft. Resume?</span>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={discardDraft} className="btn-ghost text-[12px]">Discard</button>
            <button onClick={resumeDraft} className="btn-primary py-1.5 px-3 text-[13px]">Resume draft</button>
          </div>
        </div>
      )}

      <section className="px-8 md:px-12 py-8">
        <div className="card p-2 mb-8">
          <WizardStepper
            steps={[...CLIENT_FORM_STEPS]}
            current={step}
            onJump={(i) => i <= step && setStep(i)}
          />
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-3 gap-8">
          <div className="xl:col-span-2">
            <div className="card p-8 md:p-10 min-h-[420px]">
              {step === 0 && <ProfileFields draft={draft} update={updateForm} autoFocusName />}
              {step === 1 && <GuardrailsFields draft={draft} update={updateForm} />}
              {step === 2 && (
                <DeviceStep
                  draft={draft} update={update}
                  unbound={unbound} unboundBusy={unboundBusy} refresh={refreshUnbound}
                  pickedEui={pickedEui} setPickedEui={setPickedEui}
                  euiText={eUiText} setEuiText={setEuiText}
                  euiError={euiError} setEuiError={setEuiError}
                />
              )}
              {step === 3 && (
                <ReviewStep
                  draft={draft}
                  pickedEui={pickedEui}
                  euiText={eUiText}
                  submitError={submitError}
                />
              )}
            </div>

            <div className="mt-6 flex items-center justify-between">
              <button
                disabled={step === 0 || submitBusy}
                onClick={() => setStep((s) => Math.max(0, s - 1))}
                className="btn-secondary"
              >
                <ChevronLeft size={14} /> Back
              </button>
              {step < CLIENT_FORM_STEPS.length - 1 ? (
                <button
                  disabled={!stepValid}
                  onClick={() => setStep((s) => Math.min(CLIENT_FORM_STEPS.length - 1, s + 1))}
                  className="btn-primary"
                >
                  Next <ChevronRight size={14} />
                </button>
              ) : (
                <button onClick={submit} disabled={submitBusy} className="btn-primary">
                  {submitBusy
                    ? <><Loader2 size={14} className="animate-spin" /> Creating…</>
                    : <><Check size={14} /> Create client</>}
                </button>
              )}
            </div>
          </div>

          {/* Editorial helper column */}
          <aside className="xl:col-span-1">
            <div className="sticky top-8 card p-6 bg-bone-soft/70">
              <div className="kicker mb-3">{CLIENT_FORM_STEPS[step].label}</div>
              <p className="text-[13.5px] leading-relaxed text-slate-deep">
                {step === 0 && "Names appear on every dashboard card. The condition note feeds the caregiver persona's awareness."}
                {step === 1 && "Guard rules tell the AI when to escalate (press-button-for-staff cues) and what topics to skip. Most clients need only a few."}
                {step === 2 && "Auto-pick lists Watchers seen by the server in the last ten minutes that aren't bound to anyone yet. You can also paste an EUI if you've pre-shipped a device."}
                {step === 3 && "Final check before the row lands. After creation, you'll be taken to the client page where you can verify the first conversation."}
              </p>
            </div>
          </aside>
        </div>
      </section>
    </>
  );
}

function DeviceStep(props: {
  draft: DraftState;
  update: <K extends keyof DraftState>(k: K, v: DraftState[K]) => void;
  unbound: UnboundDevice[] | null;
  unboundBusy: boolean;
  refresh: () => void;
  pickedEui: string | null;
  setPickedEui: (v: string | null) => void;
  euiText: string;
  setEuiText: (v: string) => void;
  euiError: string | null;
  setEuiError: (v: string | null) => void;
}) {
  const { draft, update, unbound, unboundBusy, refresh, pickedEui, setPickedEui,
          euiText, setEuiText, euiError, setEuiError } = props;

  const setupUrl = deviceSetupUrl();
  const showUrlPanel = draft.deviceMode !== "skip";
  const [copied, setCopied] = useState(false);

  async function copyUrl() {
    try {
      await navigator.clipboard.writeText(setupUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard might be unavailable */ }
  }

  return (
    <div className="space-y-6">
      {showUrlPanel && (
        <div className="card p-4 bg-teal-tint/40 border-teal/30">
          <div className="kicker mb-2">Point your Watcher at this URL</div>
          <code className="font-mono text-[13px] block break-all">{setupUrl}</code>
          <button onClick={copyUrl} className="btn-ghost text-[12px] mt-2 inline-flex items-center gap-1.5">
            <Copy size={12} /> {copied ? "Copied" : "Copy"}
          </button>
          <p className="text-[12px] text-slate-muted mt-2">
            Open the XiaoZhi mobile app, go to Device Settings → OTA URL, and paste this URL.
          </p>
        </div>
      )}

      <ModeRadio
        value={draft.deviceMode}
        onChange={(v) => { update("deviceMode", v); setPickedEui(null); }}
        options={[
          { v: "auto",  title: "Auto-pick from recent Watchers", desc: "Devices seen by the server in the last 10 minutes that aren't bound yet." },
          { v: "paste", title: "Paste EUI / MAC",                desc: "If you have the device's identifier handy." },
          { v: "skip",  title: "Skip — bind a device later",     desc: "Create the client now, attach the Watcher when it ships." },
        ]}
      />

      {draft.deviceMode === "auto" && (
        <div className="border-t border-slate-line/70 pt-6">
          <div className="flex items-center justify-between mb-3">
            <div className="kicker">Recent unbound Watchers</div>
            <button onClick={refresh} className="btn-ghost text-[12px]" disabled={unboundBusy}>
              <RefreshCw size={12} className={unboundBusy ? "animate-spin" : ""} /> Refresh
            </button>
          </div>
          {unbound === null && (
            <div className="text-[13px] text-slate-muted py-6">Looking…</div>
          )}
          {unbound && unbound.length === 0 && (
            <div className="card p-5 text-[13px] text-slate-muted bg-bone-soft">
              No unbound Watchers seen recently. Wake one up (long-press the button) and click Refresh, or switch to "Paste EUI" if you already know the device's identifier.
            </div>
          )}
          {unbound && unbound.length > 0 && (
            <ul className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {unbound.map((u) => {
                const active = u.eui === pickedEui;
                return (
                  <li key={u.eui}>
                    <button
                      onClick={() => setPickedEui(u.eui)}
                      className={classNames(
                        "w-full text-left card p-4 transition",
                        active ? "border-teal/60 bg-teal-tint/40" : "card-hover",
                      )}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <span className="font-mono text-[13px] tracking-tight text-slate-deep">{u.eui}</span>
                        {active && (
                          <span className="chip-teal text-[11px]"><Check size={10} /> Selected</span>
                        )}
                      </div>
                      <div className="text-[12px] text-slate-muted flex items-center justify-between">
                        <span>seen {relativeTime(u.lastSeen)}</span>
                        <span className="num">{u.sampleCount} ping{u.sampleCount === 1 ? "" : "s"}</span>
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}

      {draft.deviceMode === "paste" && (
        <div className="border-t border-slate-line/70 pt-6">
          <label htmlFor="eui" className="label">EUI or MAC</label>
          <input
            id="eui" className="input font-mono"
            placeholder="e.g. D0:CF:13:26:E9:34 or D0CF1326E934"
            value={euiText}
            onChange={(e) => { setEuiText(e.target.value); setEuiError(null); }}
            onBlur={() => setEuiError(euiText && !isValidEui(euiText) ? "Not a valid EUI/MAC. Use 12 or 16 hex characters (colons optional)." : null)}
          />
          {euiError && <div className="field-error">{euiError}</div>}
          <div className="helper">12 hex chars = MAC, 16 hex chars = full EUI-64. Colons, dashes, and spaces are ignored.</div>

          <label htmlFor="clientDeviceId" className="label mt-5">Device ID (optional)</label>
          <input
            id="clientDeviceId" className="input font-mono"
            placeholder="The client's own device identifier"
            value={draft.clientDeviceId ?? ""}
            onChange={(e) => update("clientDeviceId", e.target.value)}
          />
          <div className="helper">If the client tracks this Watcher under their own unique ID, record it here. We map it to the device alongside the MAC.</div>

          <label htmlFor="alias" className="label mt-5">Device alias (optional)</label>
          <input
            id="alias" className="input"
            placeholder="e.g. Client A — room 204"
            value={draft.deviceAlias ?? ""}
            onChange={(e) => update("deviceAlias", e.target.value)}
          />
        </div>
      )}

      {draft.deviceMode === "skip" && (
        <div className="border-t border-slate-line/70 pt-6">
          <p className="text-[13px] text-slate-muted leading-relaxed">
            You can attach a device later from this client's page. Conversations will not be recorded under this client until a Watcher is bound.
          </p>
        </div>
      )}
    </div>
  );
}

function ReviewStep({
  draft, pickedEui, euiText, submitError,
}: {
  draft: DraftState; pickedEui: string | null; euiText: string;
  submitError: string | null;
}) {
  const eui = draft.deviceMode === "auto" ? pickedEui :
              draft.deviceMode === "paste" ? normalizeEui(euiText) : null;
  return (
    <div className="space-y-6">
      <ProfileReview draft={draft} />
      <div className="border-t border-slate-line/70 pt-6">
        <ReviewRow label="Device" value={
          draft.deviceMode === "skip" ? "Skipped — bind later" :
          eui ? eui : "—"
        } mono />
        {eui && draft.clientDeviceId && (
          <ReviewRow label="Device ID" value={draft.clientDeviceId} mono />
        )}
        {eui && draft.deviceAlias && (
          <ReviewRow label="Device alias" value={draft.deviceAlias} />
        )}
      </div>

      {submitError && (
        <div role="alert" className="text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
          {submitError}
        </div>
      )}
    </div>
  );
}

function ModeRadio({
  value, onChange, options,
}: {
  value: "auto" | "paste" | "skip";
  onChange: (v: "auto" | "paste" | "skip") => void;
  options: { v: "auto" | "paste" | "skip"; title: string; desc: string }[];
}) {
  return (
    <ul className="space-y-2.5" role="radiogroup">
      {options.map((o) => {
        const active = o.v === value;
        return (
          <li key={o.v}>
            <button
              role="radio"
              aria-checked={active}
              onClick={() => onChange(o.v)}
              className={classNames(
                "w-full text-left card p-4 flex items-start gap-3 transition",
                active ? "border-teal/50 bg-teal-tint/30" : "card-hover",
              )}
            >
              <span className={classNames(
                "shrink-0 w-4 h-4 rounded-full border mt-1 grid place-items-center transition",
                active ? "border-teal bg-teal" : "border-slate-line/80 bg-white",
              )}>
                {active && <span className="w-1.5 h-1.5 rounded-full bg-white" />}
              </span>
              <span>
                <span className="block text-[15px] tracking-tight text-slate-deep font-medium">{o.title}</span>
                <span className="block mt-1 text-[13px] text-slate-muted leading-relaxed">{o.desc}</span>
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export default function NewPatientPage() {
  return (
    <RequireAuth>
      <ToastProvider>
        <AppShell>
          <OnboardWizard />
        </AppShell>
      </ToastProvider>
    </RequireAuth>
  );
}
