"use client";

/**
 * Client Detail — CareConnect / Revel / Google Calendar / Knowledge hub.
 * Identity belongs to the person (ai_agent), not a Watcher.
 * Google Calendar is read-only (private iCal URL). Nexus never writes events.
 * Knowledge assignments use the same GET/PUT /agent/{id}/knowledge-bases path
 * as Edit Client (cc_client_knowledge_base).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, BookOpen, Calendar, Check, Copy, KeyRound, Link2, Loader2, Plus, Unlink2, X, type LucideIcon } from "lucide-react";
import { apiDelete, apiGet, apiPost, apiPut, ApiError } from "@/lib/api";
import { Modal } from "@/components/Modal";
import { KnowledgeAccessFields } from "@/components/clientForm/KnowledgeAccessFields";
import type {
  CalendarEventPreview,
  ClientIntegration,
  KnowledgeBase,
  KnowledgeBaseList,
  RevelDiscoveredDevice,
  RevelVoiceAction,
} from "@/lib/types";
import {
  assignedKnowledgeIds,
  knowledgeAssignmentPutBody,
  knowledgeAssignmentStatus,
  knowledgeIdsEqual,
  toggleKnowledgeSelection,
} from "@/lib/knowledge";
import { fargoDateTime } from "@/lib/time";
import { relativeTime, classNames } from "@/lib/format";
import {
  deviceOnlineLabel,
  formatRevelDiscoverError,
  planRevelDiscover,
  revelDiscoverFailedBeforeApi,
  revelDiscoverPath,
  revelDiscoverRequestLine,
  REVEL_DISCOVER_CLICK_RECEIVED,
  REVEL_DISCOVER_DISCOVERING,
  REVEL_DISCOVER_SAVING,
} from "@/lib/revelDiscover";
import {
  addPhrase,
  phraseChipKey,
  phraseDraftInputKey,
  removePhrase,
} from "@/lib/revelPhrases";

interface Props {
  agentId: string;
  botName?: string | null;
  agentName?: string | null;
  /** Bump after Edit Client saves so the Connections row refetches assignments. */
  knowledgeTick?: number;
}

type Panel = "careconnect" | "revel" | "google_calendar" | "knowledge" | null;
type Copied = "credentials" | "endpoint" | null;

const DEFAULT_PORTAL = "https://care.nexus.warehouse-13.biz";
const DEFAULT_ASSESSMENT_PATH = "/api/v1/integrations/careconnect/assessment";

function portalOf(row: ClientIntegration | undefined): string {
  return (row?.portal || DEFAULT_PORTAL).replace(/\/$/, "");
}

function assessmentEndpointOf(row: ClientIntegration | undefined): string {
  return row?.assessmentEndpoint || `${portalOf(row)}${DEFAULT_ASSESSMENT_PATH}`;
}

function formatCalendarWhen(ev: CalendarEventPreview): string {
  if (ev.allDay) {
    const parts = ev.start.split("-").map((p) => Number(p));
    if (parts.length >= 3 && parts[0] && parts[1] && parts[2]) {
      const label = new Date(parts[0], parts[1] - 1, parts[2]).toLocaleDateString("en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
      return `${label} (all day)`;
    }
    return `${ev.start} (all day)`;
  }
  return fargoDateTime(ev.start) || ev.start;
}

function formatCalendarEvent(ev: CalendarEventPreview | null | undefined): string {
  if (!ev) return "None in the upcoming window";
  const title = ev.title?.trim() || "(untitled)";
  return `${title} — ${formatCalendarWhen(ev)}`;
}

function RevelDeviceResults({
  devices,
  discovering,
}: {
  devices: RevelDiscoveredDevice[];
  discovering: boolean;
}) {
  if (discovering) {
    return (
      <p className="text-[13px] text-slate-muted flex items-center gap-2">
        <Loader2 size={14} className="animate-spin" />
        Discovering…
      </p>
    );
  }
  if (devices.length === 0) {
    return (
      <p className="text-[13px] text-slate-muted">
        No devices yet. Save the API key, then Discover Devices.
      </p>
    );
  }
  return (
    <ul className="space-y-2">
      {devices.map((d) => (
        <li
          key={d.id}
          className="rounded-card border border-slate-line/70 bg-white px-3 py-2"
        >
          <div className="text-[13px] text-slate-deep font-medium">{d.name || d.id}</div>
          <div className="text-[12px] text-slate-muted mt-0.5">{deviceOnlineLabel(d.isOnline)}</div>
          <div className="text-[12px] text-slate-muted mt-0.5">
            {(d.tags || []).length > 0 ? (d.tags || []).join(", ") : "No tags"}
          </div>
        </li>
      ))}
    </ul>
  );
}

function ConnectionRow({
  name,
  icon: Icon,
  connected,
  status,
  detail,
  disabled,
  busy,
  onClick,
}: {
  name: string;
  icon: LucideIcon;
  connected?: boolean;
  status: string;
  detail?: string | null;
  disabled?: boolean;
  busy?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      aria-label={`${name}, ${status}`}
      className={classNames(
        "w-full flex items-start gap-2.5 px-2 py-2 rounded-card text-left transition",
        disabled
          ? "text-slate-muted cursor-not-allowed"
          : "hover:bg-white/90",
      )}
    >
      <Icon size={14} className="mt-0.5 text-slate-muted shrink-0" aria-hidden={true} />
      <span className="min-w-0 flex-1">
        <span className="block text-[13px] tracking-tight text-slate-deep leading-snug">
          {name}
        </span>
        {detail ? (
          <span className="block text-[11px] text-slate-muted mt-0.5 leading-snug">
            {detail}
          </span>
        ) : null}
      </span>
      <span
        className={classNames(
          "shrink-0 inline-flex items-center gap-1 text-[11px] tracking-tight mt-0.5",
          connected ? "text-teal-deep" : "text-slate-muted",
        )}
      >
        {busy ? <Loader2 size={11} className="animate-spin" /> : null}
        {connected ? <Check size={11} strokeWidth={2.25} aria-hidden="true" /> : null}
        {status}
      </span>
    </button>
  );
}

export function ClientIntegrations({ agentId, botName, agentName, knowledgeTick = 0 }: Props) {
  const [items, setItems] = useState<ClientIntegration[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [panel, setPanel] = useState<Panel>(null);
  const [onceSecret, setOnceSecret] = useState<string | null>(null);
  const [revelKey, setRevelKey] = useState("");
  const [revelRegKey, setRevelRegKey] = useState("");
  const [revelApiBase, setRevelApiBase] = useState("");
  const [revelDeviceId, setRevelDeviceId] = useState("");
  const [revelActions, setRevelActions] = useState<RevelVoiceAction[]>([]);
  const [phraseDraft, setPhraseDraft] = useState<Record<string, string>>({});
  const [icalUrl, setIcalUrl] = useState("");
  const [showReplaceCalendar, setShowReplaceCalendar] = useState(false);
  const [testNote, setTestNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [discoverTrace, setDiscoverTrace] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState<Copied>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const discoverClickGuard = useRef(0);
  const knowledgeRev = useRef(0);
  const knowledgeLoadGen = useRef(0);
  const [knowledgeCount, setKnowledgeCount] = useState<number | null>(null);
  const [catalog, setCatalog] = useState<KnowledgeBase[]>([]);
  const [selectedKbIds, setSelectedKbIds] = useState<number[]>([]);
  const [baselineKbIds, setBaselineKbIds] = useState<number[]>([]);
  const [kbLoading, setKbLoading] = useState(false);
  const [kbError, setKbError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const data = await apiGet<{ list: ClientIntegration[] }>(
      `/agent/${agentId}/integrations`,
    );
    setItems(data.list);
  }, [agentId]);

  useEffect(() => {
    let cancelled = false;
    refresh()
      .then(() => { if (!cancelled) setLoadError(null); })
      .catch((e) => {
        if (!cancelled) setLoadError(e instanceof ApiError ? e.message : "Failed to load integrations.");
      });
    return () => { cancelled = true; };
  }, [refresh]);

  useEffect(() => {
    let cancelled = false;
    const rev = knowledgeRev.current;
    apiGet<KnowledgeBaseList>(`/agent/${agentId}/knowledge-bases`)
      .then((data) => {
        if (cancelled || rev !== knowledgeRev.current) return;
        setKnowledgeCount(assignedKnowledgeIds(data.list).length);
      })
      .catch(() => {
        if (!cancelled && rev === knowledgeRev.current) setKnowledgeCount(0);
      });
    return () => { cancelled = true; };
  }, [agentId, knowledgeTick]);

  const cc = items?.find((i) => i.provider === "careconnect");
  const revel = items?.find((i) => i.provider === "revel");
  const gcal = items?.find((i) => i.provider === "google_calendar");

  useEffect(() => {
    if (panel !== "revel" || !revel) return;
    setRevelApiBase(revel.apiBaseUrl || "https://api.reveldigital.com");
    setRevelDeviceId(revel.deviceId || "");
    setRevelActions(revel.actions ? revel.actions.map((a) => ({ ...a, phrases: [...(a.phrases || [])] })) : []);
  }, [panel, revel?.updatedAt, revel?.lastDiscoverAt, revel?.connected, revel?.apiBaseUrl, revel?.deviceId]);

  function openPanel(next: Panel) {
    setErr(null);
    setBusy(false);
    setDiscovering(false);
    setDiscoverTrace(null);
    setCopied(null);
    setConfirmDisconnect(false);
    setRevelKey("");
    setRevelRegKey("");
    setPhraseDraft({});
    setIcalUrl("");
    setShowReplaceCalendar(false);
    setTestNote(null);
    setOnceSecret(null);
    setKbError(null);
    setPanel(next);
  }

  async function openKnowledge() {
    const gen = ++knowledgeLoadGen.current;
    openPanel("knowledge");
    setKbLoading(true);
    setKbError(null);
    try {
      const [all, assigned] = await Promise.all([
        apiGet<KnowledgeBaseList>("/knowledge-base"),
        apiGet<KnowledgeBaseList>(`/agent/${agentId}/knowledge-bases`),
      ]);
      if (gen !== knowledgeLoadGen.current) return;
      setCatalog(all.list || []);
      const ids = assignedKnowledgeIds(assigned.list);
      knowledgeRev.current += 1;
      setSelectedKbIds(ids);
      setBaselineKbIds(ids);
      setKnowledgeCount(ids.length);
    } catch (e) {
      if (gen !== knowledgeLoadGen.current) return;
      setKbError(e instanceof ApiError ? e.message : "Failed to load knowledge access.");
    } finally {
      if (gen === knowledgeLoadGen.current) setKbLoading(false);
    }
  }

  async function saveKnowledge() {
    if (busy || kbLoading) return;
    setBusy(true);
    setErr(null);
    try {
      const result = await apiPut<KnowledgeBaseList>(
        `/agent/${agentId}/knowledge-bases`,
        knowledgeAssignmentPutBody(selectedKbIds),
      );
      const ids = assignedKnowledgeIds(result.list);
      knowledgeRev.current += 1;
      setKnowledgeCount(ids.length);
      setBaselineKbIds(ids);
      setSelectedKbIds(ids);
      setPanel(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not save knowledge access.");
    } finally {
      setBusy(false);
    }
  }

  async function connectCareConnect() {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      const created = await apiPost<ClientIntegration>(
        `/agent/${agentId}/integrations/careconnect`,
      );
      setOnceSecret(created.secret ?? null);
      await refresh();
      setPanel("careconnect");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not connect CareConnect.");
      setPanel("careconnect");
    } finally {
      setBusy(false);
    }
  }

  async function onCareConnectClick() {
    if (cc?.connected) {
      openPanel("careconnect");
      return;
    }
    openPanel("careconnect");
    await connectCareConnect();
  }

  async function rotateSecret() {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      const rotated = await apiPost<ClientIntegration>(
        `/agent/${agentId}/integrations/careconnect/rotate`,
      );
      setOnceSecret(rotated.secret ?? null);
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not rotate secret.");
    } finally {
      setBusy(false);
    }
  }

  async function saveRevel() {
    if (busy || !revelKey.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await apiPut(`/agent/${agentId}/integrations/revel`, { apiKey: revelKey.trim() });
      setRevelKey("");
      setOnceSecret(null);
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not save Revel key.");
    } finally {
      setBusy(false);
    }
  }

  async function saveRevelConfig() {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      const body: Record<string, unknown> = {
        apiBaseUrl: revelApiBase.trim() || undefined,
        deviceId: revelDeviceId,
        actions: revelActions.map((a) => ({
          intent: a.intent,
          label: a.label,
          revelTag: a.revelTag || null,
          enabled: a.enabled !== false,
          phrases: a.phrases || [],
        })),
      };
      if (revelRegKey.trim()) body.registrationKey = revelRegKey.trim();
      await apiPut(`/agent/${agentId}/integrations/revel`, body);
      setRevelRegKey("");
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not save Revel display commands.");
    } finally {
      setBusy(false);
    }
  }

  async function discoverRevel() {
    // Visible first, before any guard or fetch. Never log secrets.
    setDiscoverTrace(REVEL_DISCOVER_CLICK_RECEIVED);
    console.info(`revel discover click agent=${agentId}`);
    let discoverFetchStarted = false;
    try {
      const plan = planRevelDiscover({
        agentId,
        busy: discovering,
        connected: !!revel?.connected,
        typedKey: revelKey,
      });
      console.info(`revel discover click agent=${agentId} plan=${plan.action}`);
      if (plan.action === "missing-agent") {
        setErr("Missing client id; cannot discover Revel devices.");
        return;
      }
      if (plan.action === "busy") {
        setErr("Discovery is already running.");
        return;
      }
      if (plan.action === "need-key") {
        setErr("Save the API key first, then Discover Devices.");
        return;
      }
      setBusy(true);
      setDiscovering(true);
      setErr(null);
      if (plan.action === "save-then-discover") {
        setDiscoverTrace(REVEL_DISCOVER_SAVING);
        await apiPut(`/agent/${agentId}/integrations/revel`, { apiKey: plan.apiKey });
        setRevelKey("");
      }
      const routeLine = revelDiscoverRequestLine(agentId);
      console.info(routeLine);
      setDiscoverTrace(`${REVEL_DISCOVER_DISCOVERING}\n${routeLine}`);
      discoverFetchStarted = true;
      await apiPost<ClientIntegration>(revelDiscoverPath(agentId), {});
      await refresh();
    } catch (e) {
      const safe = formatRevelDiscoverError(e);
      if (!discoverFetchStarted) {
        setDiscoverTrace(revelDiscoverFailedBeforeApi(safe));
      }
      setErr(safe);
    } finally {
      setDiscovering(false);
      setBusy(false);
    }
  }

  function onDiscoverActivate(e: { preventDefault: () => void; stopPropagation: () => void }) {
    e.preventDefault();
    e.stopPropagation();
    const now = Date.now();
    if (now - discoverClickGuard.current < 400) return;
    discoverClickGuard.current = now;
    void discoverRevel();
  }

  async function saveGoogleCalendar() {
    if (busy || !icalUrl.trim()) return;
    setBusy(true);
    setErr(null);
    setTestNote(null);
    try {
      await apiPut(`/agent/${agentId}/integrations/google-calendar`, {
        icalUrl: icalUrl.trim(),
      });
      setIcalUrl("");
      setShowReplaceCalendar(false);
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not save the calendar URL.");
    } finally {
      setBusy(false);
    }
  }

  async function testGoogleCalendar() {
    if (busy) return;
    setBusy(true);
    setErr(null);
    setTestNote(null);
    try {
      const result = await apiPost<ClientIntegration>(
        `/agent/${agentId}/integrations/google-calendar/test`,
      );
      await refresh();
      const n = result.eventCount ?? 0;
      setTestNote(
        n === 1 ? "Connection ok. 1 event in the upcoming window." : `Connection ok. ${n} events in the upcoming window.`,
      );
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not test the calendar connection.");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect(provider: "careconnect" | "revel" | "google_calendar") {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      const path = provider === "google_calendar" ? "google-calendar" : provider;
      await apiDelete(`/agent/${agentId}/integrations/${path}`);
      setConfirmDisconnect(false);
      setOnceSecret(null);
      setIcalUrl("");
      setShowReplaceCalendar(false);
      setPanel(null);
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not disconnect.");
    } finally {
      setBusy(false);
    }
  }

  async function copyCredentials() {
    const portal = portalOf(cc);
    const publicId = cc?.publicId ?? "";
    const endpoint = assessmentEndpointOf(cc);
    const lines = [
      `Portal: ${portal}`,
      `Client ID: ${publicId}`,
    ];
    if (onceSecret) {
      lines.push(`API Secret: ${onceSecret}`);
    }
    lines.push(`Assessment endpoint: ${endpoint}`);
    try {
      await navigator.clipboard.writeText(lines.join("\n"));
      setCopied("credentials");
      setTimeout(() => setCopied(null), 1500);
    } catch { /* ignore */ }
  }

  async function copyEndpoint() {
    try {
      await navigator.clipboard.writeText(assessmentEndpointOf(cc));
      setCopied("endpoint");
      setTimeout(() => setCopied(null), 1500);
    } catch { /* ignore */ }
  }

  if (loadError) {
    return (
      <div className="text-[12px] text-risk-urgent px-2">{loadError}</div>
    );
  }

  if (!items) {
    return <div className="skeleton h-24 w-full rounded-card" />;
  }

  return (
    <>
      <div className="-mx-1">
        <ConnectionRow
          name="CareConnect"
          icon={Link2}
          connected={!!cc?.connected}
          status={cc?.connected ? "Connected" : "Connect →"}
          busy={busy && panel === "careconnect" && !cc?.connected}
          onClick={onCareConnectClick}
        />
        <ConnectionRow
          name="Google Calendar"
          icon={Calendar}
          connected={!!gcal?.connected}
          status={gcal?.connected ? "Connected · Read only" : "Connect →"}
          onClick={() => openPanel("google_calendar")}
        />
        <ConnectionRow
          name="Revel"
          icon={KeyRound}
          connected={!!revel?.connected}
          status={revel?.connected ? "Connected" : "Connect →"}
          onClick={() => openPanel("revel")}
        />
        <ConnectionRow
          name="Knowledge"
          icon={BookOpen}
          status={knowledgeCount == null ? "" : knowledgeAssignmentStatus(knowledgeCount)}
          busy={knowledgeCount == null}
          onClick={() => void openKnowledge()}
        />
      </div>

      <Modal
        open={panel === "careconnect"}
        onClose={() => { if (!busy) setPanel(null); }}
        title={cc?.connected ? "CareConnect" : "Connect to CareConnect"}
        size="md"
        footer={
          confirmDisconnect ? (
            <>
              <button className="btn-secondary" disabled={busy} onClick={() => setConfirmDisconnect(false)}>
                Cancel
              </button>
              <button className="btn-danger" disabled={busy} onClick={() => disconnect("careconnect")}>
                {busy && <Loader2 size={14} className="animate-spin" />}
                Disconnect
              </button>
            </>
          ) : (
            <>
              <button className="btn-secondary" disabled={busy} onClick={() => setPanel(null)}>
                Close
              </button>
              {cc?.connected && (
                <button
                  className="btn-ghost text-risk-urgent"
                  disabled={busy}
                  onClick={() => setConfirmDisconnect(true)}
                >
                  <Unlink2 size={14} /> Disconnect
                </button>
              )}
            </>
          )
        }
      >
        {confirmDisconnect ? (
          <p className="text-[14px] text-slate-deep leading-relaxed">
            This will disconnect CareConnect from this client. The client/person, Watchers, chat history, assessments, and reminders will not be deleted.
          </p>
        ) : (
          <div className="space-y-4 text-[14px] text-slate-deep">
            {cc?.connected ? (
              <>
                <div>
                  <div className="label">Portal</div>
                  <code className="font-mono text-[13px] break-all">{portalOf(cc)}</code>
                </div>
                <div>
                  <div className="label">Client ID</div>
                  <code className="font-mono text-[13px]">{cc.publicId}</code>
                </div>
                <div>
                  <div className="label">API Secret</div>
                  {onceSecret ? (
                    <code className="font-mono text-[13px] break-all">{onceSecret}</code>
                  ) : (
                    <span className="font-mono tracking-widest">••••••••••••••••••••</span>
                  )}
                  {onceSecret && (
                    <p className="helper mt-1">Copy this secret now. It will not be shown again.</p>
                  )}
                </div>
                <div>
                  <div className="label">Assessment endpoint</div>
                  <code className="font-mono text-[13px] break-all">{assessmentEndpointOf(cc)}</code>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="btn-secondary text-[12px]" onClick={copyCredentials}>
                    <Copy size={12} /> {copied === "credentials" ? "Copied" : "Copy credentials"}
                  </button>
                  <button type="button" className="btn-secondary text-[12px]" onClick={copyEndpoint}>
                    <Copy size={12} /> {copied === "endpoint" ? "Copied" : "Copy endpoint"}
                  </button>
                  <button type="button" className="btn-secondary text-[12px]" disabled={busy} onClick={rotateSecret}>
                    {busy ? <Loader2 size={12} className="animate-spin" /> : <KeyRound size={12} />}
                    Rotate secret
                  </button>
                </div>
              </>
            ) : (
              <p className="text-slate-muted">Creating a CareConnect integration…</p>
            )}
            {err && (
              <div className="text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
                {err}
              </div>
            )}
          </div>
        )}
      </Modal>

      <Modal
        open={panel === "revel"}
        onClose={() => { if (!busy) setPanel(null); }}
        title={revel?.connected ? "Revel" : "Connect to Revel"}
        size="lg"
        footer={
          confirmDisconnect ? (
            <>
              <button className="btn-secondary" disabled={busy} onClick={() => setConfirmDisconnect(false)}>
                Cancel
              </button>
              <button className="btn-danger" disabled={busy} onClick={() => disconnect("revel")}>
                {busy && <Loader2 size={14} className="animate-spin" />}
                Disconnect
              </button>
            </>
          ) : (
            <>
              <button className="btn-secondary" disabled={busy} onClick={() => setPanel(null)}>
                Close
              </button>
              {revel?.connected && (
                <button
                  className="btn-ghost text-risk-urgent"
                  disabled={busy}
                  onClick={() => setConfirmDisconnect(true)}
                >
                  <Unlink2 size={14} /> Disconnect
                </button>
              )}
            </>
          )
        }
      >
        {confirmDisconnect ? (
          <p className="text-[14px] text-slate-deep leading-relaxed">
            This will disconnect Revel from this client. The client/person, Watchers, chat history, assessments, and reminders will not be deleted.
          </p>
        ) : (
          <div className="space-y-5 text-[14px] text-slate-deep max-h-[min(70vh,40rem)] overflow-y-auto pr-1">
            {discoverTrace ? (
              <div
                data-testid="revel-discover-trace"
                className="rounded-card border border-teal/40 bg-teal-tint px-3.5 py-2.5 text-[13px] text-slate-deep whitespace-pre-wrap font-mono"
              >
                {discoverTrace}
              </div>
            ) : null}
            {err && (
              <div className="flex items-start gap-2 text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                {err}
              </div>
            )}
            <div className="rounded-card border border-slate-line/70 bg-bone-soft/60 px-3.5 py-2.5 text-[13px] leading-relaxed">
              Voice commands require the client’s bot name.
              Example: “{botName?.trim() || "Bob"}, show my calendar”
              {!botName?.trim() ? (
                <span className="block mt-1 text-slate-muted">Set a bot name in Edit client before commands can run.</span>
              ) : null}
            </div>
            {revel?.connected && (
              <div>
                <div className="kicker mb-1">Revel: Connected</div>
                <div className="label">API key</div>
                <span className="font-mono">{revel.maskedKey || `••••••••••••${revel.secretHint || ""}`}</span>
              </div>
            )}
            <div>
              <label htmlFor="revel-key" className="label">
                {revel?.connected ? "Replace key" : "Revel API key"}
              </label>
              <input
                id="revel-key"
                className="input font-mono"
                type="password"
                autoComplete="off"
                placeholder={revel?.connected ? "Paste a new Revel API key" : "Paste the Revel API key"}
                value={revelKey}
                onChange={(e) => setRevelKey(e.target.value)}
              />
              <div className="helper">
                Paste the Developer API key from Revel Account → Developer API.
                A device registration key will not work. The full key is stored encrypted and is never shown again.
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="btn-primary text-[12px]"
                disabled={busy || revelKey.trim().length < 8}
                onClick={() => void saveRevel()}
              >
                {busy && !discovering && <Loader2 size={12} className="animate-spin" />}
                {revel?.connected ? "Replace key" : "Save key"}
              </button>
              <button
                type="button"
                className="btn-secondary text-[12px] pointer-events-auto"
                data-testid="revel-discover"
                aria-busy={discovering}
                onPointerDown={onDiscoverActivate}
                onClick={onDiscoverActivate}
              >
                {discovering ? <Loader2 size={12} className="animate-spin" /> : null}
                {discovering ? "Discovering…" : "Discover Devices"}
              </button>
            </div>
            <div className="helper">
              {revel?.connected || revelKey.trim().length >= 8
                ? "Save connection → Discover Devices. Discovery talks to CareConnect only; it does not change the display."
                : "Save the API key first, then Discover Devices."}
            </div>
            <div>
              <div className="kicker mb-2">Available devices</div>
              <RevelDeviceResults
                devices={revel?.discoveredDevices || []}
                discovering={discovering}
              />
            </div>

            {revel?.connected && (
              <>
                <div>
                  <label htmlFor="revel-base" className="label">API base URL</label>
                  <input
                    id="revel-base"
                    className="input font-mono"
                    value={revelApiBase}
                    onChange={(e) => setRevelApiBase(e.target.value)}
                    placeholder="https://api.reveldigital.com"
                  />
                </div>
                <div>
                  <label htmlFor="revel-reg" className="label">Registration key (optional)</label>
                  <input
                    id="revel-reg"
                    className="input font-mono"
                    type="password"
                    autoComplete="off"
                    placeholder={revel.registrationKeySet ? `Stored · hint ${revel.registrationKeyHint || "••••"}` : "Only if Revel requires it for this device"}
                    value={revelRegKey}
                    onChange={(e) => setRevelRegKey(e.target.value)}
                  />
                  <div className="helper">Encrypted like the API key. Leave blank to keep the stored value. Not required for tag commands unless discovery says otherwise.</div>
                </div>
                <div>
                  <label htmlFor="revel-device" className="label">Selected display</label>
                  <select
                    id="revel-device"
                    className="input"
                    value={revelDeviceId}
                    onChange={(e) => setRevelDeviceId(e.target.value)}
                  >
                    <option value="">Select a discovered display</option>
                    {(revel.discoveredDevices || []).map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}{d.isOnline === true ? " · online" : d.isOnline === false ? " · offline" : ""}
                      </option>
                    ))}
                  </select>
                  <div className="helper">Voice cannot choose a device. Discover first, then pick Display1 here.</div>
                </div>

                <div>
                  <div className="kicker mb-2">Voice display commands</div>
                  <p className="text-[12.5px] text-slate-muted mb-3 leading-relaxed">
                    Admins map each allowlisted action to a discovered Revel tag. Clients cannot edit this.
                    Live display changes are not sent until execute is approved.
                  </p>
                  <div className="space-y-3">
                    {revelActions.map((action, idx) => (
                      <div key={action.intent} className="border border-slate-line/70 rounded-card px-3.5 py-3 space-y-2.5">
                        <div className="flex items-center justify-between gap-3">
                          <div className="font-medium text-slate-deep">{action.label || action.intent}</div>
                          <label className="flex items-center gap-2 text-[12px] text-slate-muted">
                            <input
                              type="checkbox"
                              checked={action.enabled !== false}
                              onChange={(e) => {
                                const next = [...revelActions];
                                next[idx] = { ...action, enabled: e.target.checked };
                                setRevelActions(next);
                              }}
                            />
                            Enabled
                          </label>
                        </div>
                        <div>
                          <label className="label" htmlFor={`revel-tag-${action.intent}`}>Revel tag / command</label>
                          <select
                            id={`revel-tag-${action.intent}`}
                            className="input"
                            value={action.revelTag || ""}
                            onChange={(e) => {
                              const next = [...revelActions];
                              next[idx] = { ...action, revelTag: e.target.value || null };
                              setRevelActions(next);
                            }}
                          >
                            <option value="">Select a discovered tag</option>
                            {(revel.discoveredTags || []).map((tag) => (
                              <option key={tag} value={tag}>{tag}</option>
                            ))}
                            {action.revelTag && !(revel.discoveredTags || []).includes(action.revelTag) ? (
                              <option value={action.revelTag}>{action.revelTag} (saved)</option>
                            ) : null}
                          </select>
                        </div>
                        <div>
                          <div className="label">Accepted phrases</div>
                          <ul className="flex flex-wrap gap-1.5 mt-1">
                            {(action.phrases || []).length === 0 ? (
                              <li className="text-[12px] text-slate-muted">No phrases yet</li>
                            ) : (action.phrases || []).map((phrase, phraseIdx) => (
                              <li
                                key={phraseChipKey(action.intent, phraseIdx)}
                                className="inline-flex items-center gap-1 rounded-full border border-slate-line bg-white px-2 py-0.5 text-[12px]"
                              >
                                {phrase}
                                <button
                                  type="button"
                                  className="text-slate-muted hover:text-risk-urgent"
                                  aria-label={`Remove phrase ${phrase}`}
                                  onClick={() => {
                                    const next = [...revelActions];
                                    next[idx] = {
                                      ...action,
                                      phrases: removePhrase(action.phrases, phrase),
                                    };
                                    setRevelActions(next);
                                  }}
                                >
                                  <X size={12} />
                                </button>
                              </li>
                            ))}
                          </ul>
                          <div className="flex gap-2 mt-2">
                            <input
                              key={phraseDraftInputKey(action.intent)}
                              id={phraseDraftInputKey(action.intent)}
                              className="input"
                              placeholder="Add phrase"
                              value={phraseDraft[action.intent] ?? ""}
                              onChange={(e) => {
                                const value = e.target.value;
                                setPhraseDraft((d) => ({ ...d, [action.intent]: value }));
                              }}
                              onKeyDown={(e) => {
                                if (e.key !== "Enter") return;
                                e.preventDefault();
                                const text = phraseDraft[action.intent] ?? "";
                                if (!text.trim()) return;
                                const next = [...revelActions];
                                next[idx] = { ...action, phrases: addPhrase(action.phrases, text) };
                                setRevelActions(next);
                                setPhraseDraft((d) => ({ ...d, [action.intent]: "" }));
                              }}
                            />
                            <button
                              type="button"
                              className="btn-secondary text-[12px]"
                              onClick={() => {
                                const text = phraseDraft[action.intent] ?? "";
                                if (!text.trim()) return;
                                const next = [...revelActions];
                                next[idx] = { ...action, phrases: addPhrase(action.phrases, text) };
                                setRevelActions(next);
                                setPhraseDraft((d) => ({ ...d, [action.intent]: "" }));
                              }}
                            >
                              <Plus size={12} /> Add phrase
                            </button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
                <button type="button" className="btn-primary text-[12px]" disabled={busy} onClick={saveRevelConfig}>
                  {busy && <Loader2 size={12} className="animate-spin" />}
                  Save display commands
                </button>
              </>
            )}
            {err && (
              <div className="flex items-start gap-2 text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                {err}
              </div>
            )}
          </div>
        )}
      </Modal>

      <Modal
        open={panel === "google_calendar"}
        onClose={() => { if (!busy) setPanel(null); }}
        title={gcal?.connected ? "Google Calendar" : "Connect to Google Calendar"}
        size="md"
        footer={
          confirmDisconnect ? (
            <>
              <button className="btn-secondary" disabled={busy} onClick={() => setConfirmDisconnect(false)}>
                Cancel
              </button>
              <button className="btn-danger" disabled={busy} onClick={() => disconnect("google_calendar")}>
                {busy && <Loader2 size={14} className="animate-spin" />}
                Disconnect
              </button>
            </>
          ) : (
            <>
              <button className="btn-secondary" disabled={busy} onClick={() => setPanel(null)}>
                Close
              </button>
              {gcal?.connected && (
                <button
                  className="btn-ghost text-risk-urgent"
                  disabled={busy}
                  onClick={() => setConfirmDisconnect(true)}
                >
                  <Unlink2 size={14} /> Disconnect
                </button>
              )}
            </>
          )
        }
      >
        {confirmDisconnect ? (
          <p className="text-[14px] text-slate-deep leading-relaxed">
            This will disconnect Google Calendar from this client. The client/person, Watchers, chat history, assessments, and other integrations will not be deleted. Nexus will stop reading this calendar.
          </p>
        ) : (
          <div className="space-y-4 text-[14px] text-slate-deep">
            {gcal?.connected ? (
              <>
                <div>
                  <div className="kicker mb-1">Google Calendar connected</div>
                  <div className="label">Status</div>
                  <div>Connected</div>
                </div>
                <div>
                  <div className="label">Access</div>
                  <div>Read only</div>
                </div>
                {gcal.calendarHost ? (
                  <div>
                    <div className="label">Calendar host</div>
                    <code className="font-mono text-[13px]">{gcal.calendarHost}</code>
                  </div>
                ) : null}
                <div>
                  <div className="label">Last successful sync</div>
                  <div>
                    {gcal.lastSuccessfulSync
                      ? `${relativeTime(gcal.lastSuccessfulSync)} (${fargoDateTime(gcal.lastSuccessfulSync)})`
                      : "Never"}
                  </div>
                </div>
                <div>
                  <div className="label">Next event</div>
                  <div>{formatCalendarEvent(gcal.nextEvent)}</div>
                </div>
                {gcal.upcoming && gcal.upcoming.length > 0 ? (
                  <div>
                    <div className="label">Upcoming</div>
                    <ul className="mt-1 space-y-1 text-[13px]">
                      {gcal.upcoming.slice(0, 3).map((ev, idx) => (
                        <li key={`${ev.start}-${idx}`}>
                          <span className="text-slate-deep">{ev.title || "(untitled)"}</span>
                          <span className="text-slate-muted"> — {formatCalendarWhen(ev)}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {gcal.lastSyncError ? (
                  <div className="text-[13px] text-slate-muted">
                    Last sync could not reach Google. Nexus will retry on the next poll. The connection is kept.
                  </div>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="btn-secondary text-[12px]"
                    disabled={busy}
                    onClick={() => void testGoogleCalendar()}
                  >
                    {busy ? <Loader2 size={12} className="animate-spin" /> : null}
                    Test connection
                  </button>
                  <button
                    type="button"
                    className="btn-secondary text-[12px]"
                    disabled={busy}
                    onClick={() => { setShowReplaceCalendar((v) => !v); setErr(null); }}
                  >
                    Replace calendar
                  </button>
                </div>
                {testNote ? (
                  <div className="text-[13px] text-slate-deep border border-slate-200 rounded-card px-3 py-2">
                    {testNote}
                  </div>
                ) : null}
              </>
            ) : (
              <p className="text-slate-muted leading-relaxed">
                Paste the private Google Calendar iCal URL for this client. Nexus reads the feed only. It never creates, edits, or deletes calendar events.
              </p>
            )}
            {(!gcal?.connected || showReplaceCalendar) && (
              <div>
                <label htmlFor="gcal-ical" className="label">
                  Private Google Calendar iCal URL
                </label>
                <input
                  id="gcal-ical"
                  className="input font-mono"
                  type="password"
                  autoComplete="off"
                  placeholder="https://calendar.google.com/calendar/ical/…/basic.ics"
                  value={icalUrl}
                  onChange={(e) => setIcalUrl(e.target.value)}
                />
                <div className="helper">
                  Stored encrypted. Nexus will not show this URL again after save.
                </div>
                <button
                  type="button"
                  className="btn-primary text-[12px] mt-3"
                  disabled={busy || icalUrl.trim().length < 16}
                  onClick={() => void saveGoogleCalendar()}
                >
                  {busy && <Loader2 size={12} className="animate-spin" />}
                  {gcal?.connected ? "Replace calendar" : "Connect calendar"}
                </button>
              </div>
            )}
            {err && (
              <div className="flex items-start gap-2 text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                {err}
              </div>
            )}
          </div>
        )}
      </Modal>

      <Modal
        open={panel === "knowledge"}
        onClose={() => { if (!busy) setPanel(null); }}
        title={`Knowledge Access — ${agentName?.trim() || "this client"}`}
        size="md"
        footer={
          <>
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={() => { if (!busy) setPanel(null); }}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={busy || kbLoading || !!kbError || knowledgeIdsEqual(selectedKbIds, baselineKbIds)}
              onClick={() => void saveKnowledge()}
            >
              {busy && <Loader2 size={14} className="animate-spin" />}
              Save
            </button>
          </>
        }
      >
        <div className="space-y-4 text-[14px] text-slate-deep">
          <KnowledgeAccessFields
            catalog={catalog}
            selectedIds={selectedKbIds}
            loading={kbLoading}
            error={kbError}
            onToggle={(id, checked) => {
              setSelectedKbIds((prev) => toggleKnowledgeSelection(prev, id, checked));
            }}
          />
          {err && panel === "knowledge" ? (
            <div className="flex items-start gap-2 text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              {err}
            </div>
          ) : null}
        </div>
      </Modal>
    </>
  );
}
