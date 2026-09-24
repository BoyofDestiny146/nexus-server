"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  Calendar, ChevronLeft, MessageCircle, Cpu, Loader2, RefreshCw, Trash2, AlertTriangle, Copy,
  Pencil, Check, X, Plus, type LucideIcon,
} from "lucide-react";
import { apiGet, apiPost, apiPatch, apiDelete, ApiError, getCurrentUser } from "@/lib/api";
import type {
  AgentDetail, ChatSession, ChatMessage, MedicalAssessment, DeviceRow,
} from "@/lib/types";
import { deviceSetupUrl } from "@/lib/serverConfig";
import { classNames, dayLabel, relativeTime, shortTime } from "@/lib/format";
import { isSystemChat, parseGcalTimeline } from "@/lib/calendarTimeline";
import { useLiveChat } from "@/lib/useLiveChat";
import { AppShell } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RiskBadge } from "@/components/RiskDot";
import { Sparkline } from "@/components/Sparkline";
import { EmptyState } from "@/components/EmptyState";
import { Modal } from "@/components/Modal";
import { ToastProvider, useToast } from "@/components/Toast";
import { ClientIntegrations } from "@/components/ClientIntegrations";

const WATCHER_ONLINE_MS = 5 * 60_000;

function isWatcherOnline(lastConnectedAt: string | null | undefined): boolean {
  if (!lastConnectedAt) return false;
  const t = new Date(lastConnectedAt).getTime();
  if (Number.isNaN(t)) return false;
  return Date.now() - t < WATCHER_ONLINE_MS;
}

function deviceTitle(d: DeviceRow): string {
  const alias = d.alias?.trim();
  if (alias) return alias;
  const board = (d.board || "").toLowerCase();
  if (d.deviceType === "W1-A" || board.includes("sensecap") || d.firmwareType === "xiaozhi") {
    return "SenseCAP Watcher";
  }
  return "Watcher";
}

function shortSessionId(id: string): string {
  const compact = id.replace(/-/g, "");
  if (compact.length <= 10) return id;
  return `${id.slice(0, 8)}…`;
}

function SectionLabel({ children }: { children: ReactNode }) {
  return <div className="kicker px-2 mb-1.5">{children}</div>;
}

function PanelAction({
  icon: Icon,
  label,
  onClick,
}: {
  icon: LucideIcon;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full flex items-center gap-2.5 px-2 py-2 rounded-card text-[13px] tracking-tight text-slate-deep hover:bg-white/90 transition"
    >
      <Icon size={14} className="text-slate-muted shrink-0" />
      {label}
    </button>
  );
}


function PatientDetailView({ id }: { id: string }) {
  const router = useRouter();
  const toast = useToast();
  const me = getCurrentUser();
  const isRoot = me?.role === "root";
  const [deleteOpen, setDeleteOpen] = useState(false);

  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [agentError, setAgentError] = useState<string | null>(null);

  const [sessions, setSessions] = useState<ChatSession[] | null>(null);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loadingMsgs, setLoadingMsgs] = useState(false);

  const [latest, setLatest] = useState<MedicalAssessment | null>(null);
  const [history, setHistory] = useState<MedicalAssessment[]>([]);
  const [regenBusy, setRegenBusy] = useState(false);

  const [devices, setDevices] = useState<DeviceRow[]>([]);

  const [editOpen, setEditOpen] = useState(false);
  const [attachOpen, setAttachOpen] = useState(false);

  const live = useLiveChat(id);

  // re-fetch the agent + sessions + devices after a successful mutation
  // (Edit Client / Attach Device). Keeps the UI in sync without a full reload.
  async function refreshAgentAndDevices() {
    try {
      const [a, s, d] = await Promise.allSettled([
        apiGet<AgentDetail>(`/agent/${id}`),
        apiGet<ChatSession[] | { list: ChatSession[] }>(`/agent/${id}/sessions`),
        apiGet<DeviceRow[]>(`/device/bind/${id}`),
      ]);
      if (a.status === "fulfilled") setAgent(a.value);
      if (s.status === "fulfilled") {
        const arr = Array.isArray(s.value) ? s.value : (s.value as { list: ChatSession[] }).list;
        if (arr) setSessions(arr);
      }
      if (d.status === "fulfilled") setDevices(d.value ?? []);
    } catch { /* ignore */ }
  }

  // Initial load: agent details + sessions + latest assessment + history + devices
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [a, s, l, h, d] = await Promise.allSettled([
          apiGet<AgentDetail>(`/agent/${id}`),
          apiGet<ChatSession[] | { list: ChatSession[] }>(`/agent/${id}/sessions`),
          apiGet<MedicalAssessment | null>(`/agent/${id}/assessment/latest`),
          apiGet<MedicalAssessment[]>(`/agent/${id}/assessment/history?days=14`),
          apiGet<DeviceRow[]>(`/device/bind/${id}`),
        ]);
        if (cancelled) return;
        if (a.status === "fulfilled") setAgent(a.value);
        else setAgentError(a.reason instanceof ApiError ? a.reason.message : "Failed to load client.");
        if (s.status === "fulfilled") {
          const arr = Array.isArray(s.value) ? s.value : (s.value as { list: ChatSession[] }).list;
          setSessions(arr ?? []);
          if (arr && arr[0]) setActiveSession(arr[0].sessionId);
        }
        if (l.status === "fulfilled") setLatest(l.value);
        if (h.status === "fulfilled") setHistory(h.value ?? []);
        if (d.status === "fulfilled") setDevices(d.value ?? []);
      } catch {
        /* per-call errors handled above */
      }
    }
    load();
    return () => { cancelled = true; };
  }, [id]);

  // Load messages when active session changes
  useEffect(() => {
    if (!activeSession) { setMessages([]); return; }
    setLoadingMsgs(true);
    apiGet<ChatMessage[]>(`/agent/${id}/chat-history/${activeSession}`)
      .then((m) => setMessages(m ?? []))
      .catch(() => setMessages([]))
      .finally(() => setLoadingMsgs(false));
  }, [id, activeSession]);

  // Apply live chat.turn frames.  Three things happen on every batch:
  //   1. The sessions list is reconciled — counts bumped for known sessions,
  //      brand-new sessions prepended.  Without this, a new conversation
  //      started on the Watcher would never appear in the left panel until
  //      the user manually navigated away and back.
  //   2. If the active session matches one of the incoming sessions, the
  //      new messages are appended to the visible transcript.
  //   3. If no session was active yet, OR the user is currently looking at
  //      what *was* the most-recent session, follow the new conversation
  //      automatically.  If they explicitly picked an older session, leave
  //      them on it (don't yank the view away from someone reading history).
  const lastProcessedRef = useRef(0);
  useEffect(() => {
    if (live.newMessages.length === 0) return;
    if (live.newMessages.length === lastProcessedRef.current) return;
    const fresh = live.newMessages.slice(lastProcessedRef.current);
    lastProcessedRef.current = live.newMessages.length;

    // group by session
    const bySession = new Map<string, typeof fresh>();
    for (const m of fresh) {
      if (!bySession.has(m.sessionId)) bySession.set(m.sessionId, []);
      bySession.get(m.sessionId)!.push(m);
    }

    // 1. reconcile sessions list (create missing, bump counts, re-sort)
    let newestArrivedSession = activeSession;
    let newestArrivedTs = 0;
    setSessions((prev) => {
      const list = prev ? [...prev] : [];
      for (const [sid, msgs] of bySession) {
        const idx = list.findIndex((s) => s.sessionId === sid);
        const ts = Math.max(...msgs.map((m) => new Date(m.createdAt).getTime()));
        if (ts > newestArrivedTs) {
          newestArrivedTs = ts;
          newestArrivedSession = sid;
        }
        if (idx >= 0) {
          list[idx] = {
            ...list[idx],
            messageCount: list[idx].messageCount + msgs.length,
            createdAt: list[idx].createdAt, // preserve session start
          };
        } else {
          list.unshift({
            sessionId: sid,
            agentId: id,
            createdAt: msgs[0].createdAt,
            messageCount: msgs.length,
          } as ChatSession);
        }
      }
      list.sort(
        (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
      );
      return list;
    });

    // 2. append messages for the visible session
    const forActive = activeSession ? bySession.get(activeSession) : undefined;
    if (forActive && forActive.length > 0) {
      setMessages((prev) => {
        const known = new Set(prev.map((m) => m.id));
        const added = forActive.filter((m) => !known.has(m.id));
        if (added.length === 0) return prev;
        return [...prev, ...added].sort((a, b) => a.id - b.id);
      });
    }

    // 3. auto-follow the new session if we weren't pinned to an older one.
    //    "Pinned" = activeSession is set AND it's not the current top of the
    //    list (i.e., the user explicitly clicked an older row).
    setSessions((prev) => {
      if (!prev || prev.length === 0) return prev;
      const topNow = prev[0].sessionId;
      const wasOnTop = !activeSession || activeSession === topNow;
      if (wasOnTop && newestArrivedSession && newestArrivedSession !== activeSession) {
        // Defer the state update to avoid setState-in-render warnings.
        queueMicrotask(() => setActiveSession(newestArrivedSession));
      }
      return prev;
    });
  }, [live.newMessages, activeSession, id]);

  // Belt-and-braces: once a minute, refetch sessions in case a WS frame
  // was dropped during reconnect. Cheap query, idempotent merge.
  useEffect(() => {
    const t = window.setInterval(async () => {
      try {
        const s = await apiGet<ChatSession[] | { list: ChatSession[] }>(
          `/agent/${id}/sessions`,
        );
        const arr = Array.isArray(s) ? s : (s as { list: ChatSession[] }).list;
        if (arr) setSessions(arr);
      } catch { /* ignore */ }
    }, 60_000);
    return () => window.clearInterval(t);
  }, [id]);

  // Apply live assessment.updated frames
  useEffect(() => {
    if (live.liveAssessment) {
      setLatest(live.liveAssessment);
      setHistory((prev) => {
        const without = prev.filter((a) => a.id !== live.liveAssessment!.id);
        return [...without, live.liveAssessment!].sort(
          (a, b) => new Date(a.forDate).getTime() - new Date(b.forDate).getTime(),
        );
      });
    }
  }, [live.assessmentTick, live.liveAssessment]);

  // Auto-scroll on new messages
  const scrollerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!scrollerRef.current) return;
    scrollerRef.current.scrollTo({ top: scrollerRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length]);

  async function regenerate() {
    if (regenBusy || !isRoot) return;
    setRegenBusy(true);
    try {
      const next = await apiPost<MedicalAssessment>(`/agent/${id}/assessment/regenerate`);
      setLatest(next);
      setHistory((prev) => [...prev.filter((p) => p.id !== next.id), next]
        .sort((a, b) => new Date(a.forDate).getTime() - new Date(b.forDate).getTime()));
    } catch (e) {
      // surface failure but don't crash
      console.error("regenerate failed:", e);
    } finally {
      setRegenBusy(false);
    }
  }

  // Group messages by day for date dividers
  const grouped = useMemo(() => {
    const out: Array<{ day: string; items: ChatMessage[] }> = [];
    for (const m of messages) {
      const d = dayLabel(m.createdAt);
      const last = out[out.length - 1];
      if (last && last.day === d) last.items.push(m);
      else out.push({ day: d, items: [m] });
    }
    return out;
  }, [messages]);

  if (agentError) {
    return (
      <EmptyState
        title="Could not load this client"
        body={agentError}
        action={<Link href="/patients" className="btn-secondary">Back to roster</Link>}
      />
    );
  }
  if (!agent) {
    return (
      <div className="px-8 md:px-12 py-12">
        <div className="skeleton h-12 w-64 mb-6" />
        <div className="skeleton h-72 w-full" />
      </div>
    );
  }

  return (
    <>
      <header className="px-6 md:px-10 pt-7 pb-5 border-b border-slate-line/70">
        <button
          onClick={() => router.push("/patients")}
          className="inline-flex items-center gap-1.5 text-[12px] uppercase tracking-[0.12em] text-slate-muted hover:text-slate-deep transition mb-4"
        >
          <ChevronLeft size={14} /> Roster
        </button>
        <div className="flex items-start justify-between gap-6 min-w-0">
          <div className="min-w-0">
            <div className="flex items-center gap-2.5 mb-2">
              <span className="kicker">Client detail</span>
              <LiveBadge status={live.status} />
            </div>
            <h1 className="display-1 text-slate-deep leading-[1.05]">{agent.agentName}</h1>
            <div className="mt-3 flex flex-wrap items-center gap-x-2.5 gap-y-1.5 text-[12px] text-slate-muted">
              <RiskBadge
                level={latest?.riskLevel ?? null}
                className="px-2.5 py-1 text-[12px]"
              />
              {latest?.confidence != null && (
                <>
                  <span className="text-slate-line" aria-hidden="true">·</span>
                  <span className="num">confidence {(latest.confidence * 100).toFixed(0)}%</span>
                </>
              )}
              {agent.langCode && (
                <>
                  <span className="text-slate-line" aria-hidden="true">·</span>
                  <span className="font-mono uppercase text-slate-muted">{agent.langCode}</span>
                </>
              )}
              <span className="text-slate-line" aria-hidden="true">·</span>
              <span className="font-mono text-[11px] text-slate-muted/70 truncate" title={agent.id}>
                id {agent.id.slice(0, 12)}…
              </span>
            </div>
          </div>
        </div>
      </header>

      {/* Three-pane layout: controls | conversation | assessment */}
      <section className="flex flex-col xl:flex-row min-h-[calc(100vh-11.5rem)] xl:h-[calc(100vh-11.5rem)] xl:overflow-hidden">
        {/* Left: client controls + sessions */}
        <aside className="w-full xl:w-[17.5rem] xl:shrink-0 border-b xl:border-b-0 xl:border-r border-slate-line/70 bg-bone-soft/60 flex flex-col xl:h-full xl:min-h-0">
          <div className="px-3 pt-5 pb-4 space-y-5 border-b border-slate-line/70">
            <div>
              <SectionLabel>Client</SectionLabel>
              <PanelAction icon={Pencil} label="Edit client" onClick={() => setEditOpen(true)} />
              <PanelAction icon={Plus} label="Attach device" onClick={() => setAttachOpen(true)} />
            </div>
            <div>
              <SectionLabel>Connections</SectionLabel>
              <ClientIntegrations agentId={id} />
            </div>
            <div>
              <SectionLabel>Devices</SectionLabel>
              {devices.length === 0 ? (
                <p className="px-2 py-1.5 text-[12px] text-slate-muted leading-relaxed">
                  No Watcher bound to this client.
                </p>
              ) : (
                <ul className="space-y-2">
                  {devices.map((d) => {
                    const online = isWatcherOnline(d.lastConnectedAt);
                    return (
                      <li
                        key={d.id}
                        className="rounded-card border border-slate-line/70 bg-white/80 px-2.5 py-2"
                      >
                        <div className="text-[13px] tracking-tight text-slate-deep leading-snug">
                          {deviceTitle(d)}
                        </div>
                        <div className="mt-0.5 font-mono text-[11px] text-slate-muted truncate">
                          {d.macAddress}
                        </div>
                        <div className="mt-1.5 flex items-center gap-1.5 text-[11px]">
                          <span
                            className={classNames(
                              "w-1.5 h-1.5 rounded-full",
                              online ? "bg-teal" : "bg-slate-line",
                            )}
                            aria-hidden="true"
                          />
                          <span className={online ? "text-teal-deep" : "text-slate-muted"}>
                            {online ? "Online" : "Bound"}
                          </span>
                          {!online && d.lastConnectedAt ? (
                            <span className="text-slate-muted/80">
                              · {relativeTime(d.lastConnectedAt)}
                            </span>
                          ) : null}
                        </div>
                        <DeviceIdEditor device={d} onSaved={refreshAgentAndDevices} />
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>

          <div className="px-3 pt-4 pb-2 flex items-baseline justify-between gap-2">
            <div className="kicker">Sessions</div>
            <span className="text-[11px] tracking-tight text-slate-muted num">
              {sessions?.length ?? 0}
            </span>
          </div>
          <div className="px-2 pb-3 flex-1 overflow-y-auto min-h-[8rem]">
            {!sessions && (
              <div className="px-2 py-3 text-[12px] text-slate-muted">Loading…</div>
            )}
            {sessions && sessions.length === 0 && (
              <div className="px-2 py-3 text-[13px] text-slate-muted leading-relaxed">
                No conversations yet. The first will appear here when the Watcher is used.
              </div>
            )}
            <ul className="space-y-0.5">
              {sessions?.map((s) => {
                const active = s.sessionId === activeSession;
                return (
                  <li key={s.sessionId}>
                    <button
                      onClick={() => setActiveSession(s.sessionId)}
                      className={classNames(
                        "w-full text-left px-2.5 py-2 rounded-card border transition",
                        active
                          ? "bg-white border-slate-line text-slate-deep"
                          : "border-transparent text-slate hover:bg-white/70",
                      )}
                    >
                      <div className="flex items-baseline justify-between gap-2 text-[13px] tracking-tight">
                        <span className={active ? "text-slate-deep" : ""}>
                          {relativeTime(s.createdAt)}
                        </span>
                        <span className="text-[11px] text-slate-muted num shrink-0">
                          {s.messageCount}
                        </span>
                      </div>
                      <div className="mt-0.5 font-mono text-[10px] tracking-tight text-slate-muted/80 truncate">
                        {shortSessionId(s.sessionId)}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>

          {isRoot && (
            <div className="mt-auto px-3 py-3 border-t border-slate-line/70">
              <button
                type="button"
                onClick={() => setDeleteOpen(true)}
                className="text-[12px] text-slate-muted hover:text-risk-urgent transition px-2 py-1"
              >
                Delete client
              </button>
            </div>
          )}
        </aside>

        {/* Center: chat transcript */}
        <div
          ref={scrollerRef}
          className="flex-1 min-w-0 px-6 md:px-10 py-7 overflow-y-auto bg-white xl:h-full xl:min-h-0"
        >
          {loadingMsgs && (
            <div className="space-y-3 max-w-2xl">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="skeleton h-12 w-2/3" />
              ))}
            </div>
          )}

          {!loadingMsgs && grouped.length === 0 && (
            <EmptyState
              icon={MessageCircle}
              title="No messages in this session"
              body="When the client speaks to the Watcher, the conversation will stream in here."
            />
          )}

          {!loadingMsgs && grouped.map((group, gi) => (
            <div key={gi} className="mb-7 max-w-2xl mx-auto">
              <div className="flex items-center gap-3 mb-3.5">
                <div className="kicker">{group.day}</div>
                <div className="flex-1 h-px grid-rule" />
              </div>
              <ol className="space-y-4">
                {group.items.map((m) => {
                  const gcal = parseGcalTimeline(m.content);
                  if (isSystemChat(m.chatType) || gcal) {
                    const when = shortTime(gcal?.occurrenceStart || m.createdAt);
                    const spoken = (gcal?.spokenText || m.content || "").trim();
                    return (
                      <li key={m.id} className="flex justify-center">
                        <div className="w-full max-w-[min(100%,28rem)] border border-dashed border-slate-line bg-bone-soft/70 rounded-card px-3.5 py-2.5">
                          <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.14em] text-slate-muted">
                            <Calendar size={12} className="shrink-0 text-slate-muted" aria-hidden />
                            <span>Calendar reminder{when ? ` · ${when}` : ""}</span>
                          </div>
                          {spoken ? (
                            <div className="mt-1.5 text-[13.5px] leading-relaxed text-slate-deep whitespace-pre-wrap">
                              {spoken}
                            </div>
                          ) : null}
                        </div>
                      </li>
                    );
                  }
                  const fromCaregiver = m.chatType === 2;
                  return (
                    <li key={m.id}
                        className={classNames(
                          "flex",
                          fromCaregiver ? "justify-end" : "justify-start",
                        )}>
                      <div className={classNames(
                        "max-w-[min(100%,22rem)] sm:max-w-[70%] flex flex-col gap-1",
                        fromCaregiver ? "items-end" : "items-start",
                      )}>
                        <div className={classNames(
                          "px-3.5 py-2 rounded-2xl text-[14px] leading-[1.45]",
                          fromCaregiver
                            ? "bg-teal-tint border border-teal/15 text-slate-deep rounded-tr-sm"
                            : "bg-bone-soft border border-slate-line/70 text-slate-deep rounded-tl-sm",
                        )}>
                          {(() => {
                            const ph = m.content.match(/^\s*\[\[photo:([^\]]+)\]\]\s*([\s\S]*)$/);
                            if (ph) {
                              return (
                                <>
                                  {/* eslint-disable-next-line @next/next/no-img-element */}
                                  <img
                                    src={ph[1]}
                                    alt="Captured by the Watcher camera"
                                    className="rounded-lg mb-1.5 max-h-56 w-auto border border-slate-line/60"
                                  />
                                  {ph[2] ? <div>{ph[2]}</div> : null}
                                </>
                              );
                            }
                            return m.content;
                          })()}
                        </div>
                        <div className={classNames(
                          "text-[10px] uppercase tracking-[0.12em] text-slate-muted px-1",
                          fromCaregiver ? "text-right" : "text-left",
                        )}>
                          {fromCaregiver ? "caregiver" : "client"} · {shortTime(m.createdAt)}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ol>
            </div>
          ))}
        </div>

        {/* Right: risk panel */}
        <aside
          className={classNames(
            "w-full xl:w-[16.5rem] xl:shrink-0 border-t xl:border-t-0 xl:border-l border-slate-line/70 bg-bone-soft/60 px-5 py-6 transition xl:h-full xl:min-h-0 xl:overflow-y-auto",
            live.assessmentTick > 0 && "ring-1 ring-teal/30",
          )}
        >
          <div className="kicker mb-2">14-day risk</div>
          <Sparkline data={history} width={232} height={48} />

          <div className="mt-6">
            <div className="kicker mb-3">Latest assessment</div>
            {latest ? (
              <>
                <div className="flex items-center gap-3">
                  <div className="display-2 text-slate-deep capitalize leading-none">
                    {latest.riskLevel}
                  </div>
                  {latest.confidence != null && (
                    <ConfidenceRing value={latest.confidence} level={latest.riskLevel} />
                  )}
                </div>
                <div className="text-[12px] tracking-tight text-slate-muted num mt-2">
                  {relativeTime(latest.generatedAt)} · {latest.sourceMsgCount}{" "}
                  {latest.sourceMsgCount === 1 ? "message" : "messages"}
                </div>

                {latest.concerns.length > 0 && (
                  <div className="mt-6">
                    <div className="kicker mb-2">Concerns</div>
                    <ul className="space-y-1.5 text-[13.5px] text-slate-deep leading-relaxed">
                      {latest.concerns.map((c, i) => (
                        <li key={i} className="flex gap-2">
                          <span className="text-slate-muted">·</span>
                          <span>{c}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {latest.recommendations.length > 0 && (
                  <div className="mt-6">
                    <div className="kicker mb-2">Recommendations</div>
                    <ul className="space-y-1.5 text-[13.5px] text-slate-deep leading-relaxed">
                      {latest.recommendations.map((r, i) => (
                        <li key={i} className="flex gap-2">
                          <span className="text-teal shrink-0">→</span>
                          <span>{r}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {isRoot && (
                  <button
                    onClick={regenerate}
                    disabled={regenBusy}
                    className="btn-ghost mt-6 text-[12px] text-slate-muted hover:text-slate-deep px-0"
                  >
                    {regenBusy
                      ? <><Loader2 size={12} className="animate-spin" /> Regenerating…</>
                      : <><RefreshCw size={12} /> Regenerate</>}
                  </button>
                )}
              </>
            ) : (
              <div className="text-[14px] text-slate-muted leading-relaxed">
                No assessment yet. {isRoot && (
                  <button
                    className="text-teal-deep hover:underline"
                    onClick={regenerate}
                    disabled={regenBusy}
                  >
                    Generate one →
                  </button>
                )}
              </div>
            )}
          </div>
        </aside>
      </section>

      <DeletePatientModal
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        agentId={id}
        agentName={agent.agentName ?? "this client"}
        onDeleted={(name, cascade) => {
          setDeleteOpen(false);
          toast.push(
            `${name} deleted. ${cascade.devicesUnbound} device${cascade.devicesUnbound === 1 ? "" : "s"} unbound, ${cascade.chatRowsDeleted} chat rows + ${cascade.assessmentsDeleted} assessments removed.`,
            "success",
          );
          router.push("/patients");
        }}
      />

      <EditPatientModal
        open={editOpen}
        onClose={() => setEditOpen(false)}
        agent={agent}
        onSaved={async () => {
          setEditOpen(false);
          toast.push("Client updated.", "success");
          await refreshAgentAndDevices();
        }}
      />

      <AttachDeviceModal
        open={attachOpen}
        onClose={() => setAttachOpen(false)}
        agentId={id}
        onAttached={async (res) => {
          setAttachOpen(false);
          if (res.previousAgentId) {
            toast.push("Device was unbound from previous client.", "info");
          } else {
            toast.push("Device attached.", "success");
          }
          await refreshAgentAndDevices();
        }}
      />
    </>
  );
}

function LiveBadge({ status }: { status: import("@/lib/useLiveChat").LiveChatStatus }) {
  if (status === "live") {
    return (
      <span className="inline-flex items-center gap-1.5 text-[10px] uppercase tracking-[0.16em] text-teal-deep">
        <span className="live-dot" /> Live
      </span>
    );
  }
  if (status === "polling") {
    return (
      <span
        title="Live socket unavailable — refreshing every 2s via REST."
        className="inline-flex items-center gap-1.5 text-[10px] uppercase tracking-[0.16em] text-slate-muted"
      >
        <Loader2 size={10} className="animate-spin" /> Polling
      </span>
    );
  }
  if (status === "disconnected") {
    return (
      <span className="inline-flex items-center gap-1.5 text-[10px] uppercase tracking-[0.16em] text-risk-urgent">
        <AlertTriangle size={10} /> Disconnected
      </span>
    );
  }
  // "connecting"
  return (
    <span className="inline-flex items-center gap-1.5 text-[10px] uppercase tracking-[0.16em] text-slate-muted">
      <Loader2 size={10} className="animate-spin" /> Connecting
    </span>
  );
}

function EditPatientModal({
  open, onClose, agent, onSaved,
}: {
  open: boolean;
  onClose: () => void;
  agent: AgentDetail;
  onSaved: () => void;
}) {
  const [name, setName] = useState(agent.agentName ?? "");
  const [persona, setPersona] = useState(agent.systemPrompt ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setName(agent.agentName ?? "");
      setPersona(agent.systemPrompt ?? "");
      setErr(null);
      setBusy(false);
    }
  }, [open, agent]);

  const dirty =
    (name.trim() !== (agent.agentName ?? "").trim()) ||
    ((persona ?? "") !== (agent.systemPrompt ?? ""));

  async function submit() {
    if (busy || !dirty) return;
    setBusy(true); setErr(null);
    const body: { name?: string; systemPrompt?: string } = {};
    if (name.trim() !== (agent.agentName ?? "").trim()) body.name = name.trim();
    if ((persona ?? "") !== (agent.systemPrompt ?? "")) body.systemPrompt = persona;
    try {
      await apiPatch<{ agentId: string; updated: number; fields: string[] }>(
        `/agent/${agent.id}`,
        body,
      );
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Update failed.");
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open} onClose={onClose} title="Edit client" size="lg"
      footer={
        <>
          <button onClick={onClose} className="btn-secondary">Cancel</button>
          <button onClick={submit} disabled={!dirty || busy} className="btn-primary">
            {busy && <Loader2 size={14} className="animate-spin" />}
            Save changes
          </button>
        </>
      }
    >
      <div className="space-y-5">
        <div>
          <label htmlFor="edit-name" className="label">Client name</label>
          <input
            id="edit-name" className="input text-[16px]"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
        </div>
        <div>
          <label htmlFor="edit-persona" className="label">Persona override</label>
          <textarea
            id="edit-persona" rows={10} className="input"
            placeholder="System prompt that frames the caregiver. Leave blank to use the global persona."
            value={persona}
            onChange={(e) => setPersona(e.target.value)}
          />
          <div className="helper">Stored on the agent row. Affects the LLM's tone for every conversation with this client.</div>
        </div>
        {err && (
          <div className="text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
            {err}
          </div>
        )}
      </div>
    </Modal>
  );
}

function DeviceIdEditor({
  device, onSaved,
}: {
  device: DeviceRow;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  function begin() {
    setValue(device.clientDeviceId ?? "");
    setEditing(true);
  }

  async function save() {
    if (busy) return;
    setBusy(true);
    try {
      await apiPatch(`/device/${device.id}/client-id`, {
        clientDeviceId: value.trim() || null,
      });
      setEditing(false);
      toast.push(value.trim() ? `Device ID set to ${value.trim()}` : "Device ID cleared", "success");
      onSaved();
    } catch (e) {
      toast.push(e instanceof ApiError ? e.message : "Could not update Device ID.", "error");
    } finally {
      setBusy(false);
    }
  }

  if (!editing) {
    return (
      <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-slate-muted">
        <span className="shrink-0">ID</span>
        <span className="font-mono truncate text-slate-deep/80">
          {device.clientDeviceId ?? "—"}
        </span>
        <button
          type="button"
          onClick={begin}
          aria-label="Edit device ID"
          className="p-0.5 rounded text-slate-muted/70 hover:text-slate-deep hover:bg-slate-line/40"
        >
          <Pencil size={11} />
        </button>
      </div>
    );
  }

  return (
    <div className="mt-1 flex items-center gap-1">
      <input
        className="input font-mono !h-6 !text-[11px] !px-1.5 min-w-0 flex-1"
        maxLength={64}
        autoFocus
        placeholder="external device id"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") save();
          if (e.key === "Escape") setEditing(false);
        }}
        disabled={busy}
      />
      <button
        type="button"
        onClick={save}
        disabled={busy}
        aria-label="Save device ID"
        className="p-1 rounded text-teal hover:bg-teal-tint"
      >
        {busy ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
      </button>
      <button
        type="button"
        onClick={() => setEditing(false)}
        disabled={busy}
        aria-label="Cancel"
        className="p-1 rounded text-slate-muted hover:bg-slate-line/40"
      >
        <X size={12} />
      </button>
    </div>
  );
}

function normalizeEui(raw: string): string {
  return raw.replace(/[\s:\-]/g, "").toUpperCase();
}
function isValidEui(raw: string): boolean {
  const n = normalizeEui(raw);
  return /^[0-9A-F]{12}$|^[0-9A-F]{16}$/.test(n);
}

interface AttachResponse {
  deviceId: string;
  eui: string;
  agentId: string;
  alias: string | null;
  previousAgentId: string | null;
  deviceType: string | null;
  firmwareType: string | null;
}

function AttachDeviceModal({
  open, onClose, agentId, onAttached,
}: {
  open: boolean;
  onClose: () => void;
  agentId: string;
  onAttached: (res: AttachResponse) => void;
}) {
  const [euiText, setEuiText] = useState("");
  const [alias, setAlias] = useState("");
  const [force, setForce] = useState(false);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setEuiText(""); setAlias(""); setForce(false);
      setCopied(false); setErr(null); setBusy(false);
    }
  }, [open]);

  const valid = isValidEui(euiText);
  const setupUrl = deviceSetupUrl();

  async function submit() {
    if (busy || !valid) return;
    setBusy(true); setErr(null);
    try {
      const res = await apiPost<AttachResponse>("/device/attach", {
        agentId,
        eui: normalizeEui(euiText),
        alias: alias.trim() || undefined,
        force,
        deviceType: "W1-A",
        firmwareType: "xiaozhi",
      });
      onAttached(res);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not attach device.");
      setBusy(false);
    }
  }

  async function copyUrl() {
    try {
      await navigator.clipboard.writeText(setupUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard might be unavailable in older browsers — silent */
    }
  }

  return (
    <Modal
      open={open} onClose={onClose} title="Attach a Watcher" size="md"
      footer={
        <>
          <button onClick={onClose} className="btn-secondary">Cancel</button>
          <button onClick={submit} disabled={!valid || busy} className="btn-primary">
            {busy && <Loader2 size={14} className="animate-spin" />}
            <Cpu size={14} /> Attach device
          </button>
        </>
      }
    >
      <div className="space-y-5">
        <div>
          <label htmlFor="attach-eui" className="label">EUI or MAC</label>
          <input
            id="attach-eui" className="input font-mono"
            placeholder="e.g. D0:CF:13:26:E9:34 or D0CF1326E934"
            value={euiText}
            onChange={(e) => setEuiText(e.target.value.toUpperCase())}
            autoFocus
          />
          <div className="helper">12 hex chars = MAC, 16 hex chars = full EUI-64. Colons, dashes, and spaces are ignored.</div>
        </div>
        <div>
          <label htmlFor="attach-alias" className="label">Device alias (optional)</label>
          <input
            id="attach-alias" className="input"
            placeholder="e.g. Client A — room 204"
            value={alias}
            onChange={(e) => setAlias(e.target.value)}
          />
        </div>
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
        <label className="flex items-start gap-3 text-[13.5px] text-slate-deep cursor-pointer">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={force}
            onChange={(e) => setForce(e.target.checked)}
          />
          <span>
            <span className="block">Force re-bind from another client</span>
            <span className="block text-[12px] text-slate-muted leading-relaxed">
              If this device is currently bound to someone else, unbind it first instead of failing.
            </span>
          </span>
        </label>
        {err && (
          <div className="text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
            {err}
          </div>
        )}
      </div>
    </Modal>
  );
}

function DeletePatientModal({
  open, onClose, agentId, agentName, onDeleted,
}: {
  open: boolean;
  onClose: () => void;
  agentId: string;
  agentName: string;
  onDeleted: (name: string, cascade: { devicesUnbound: number; chatRowsDeleted: number; assessmentsDeleted: number }) => void;
}) {
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const matches = confirm.trim() === agentName.trim();

  // reset state every time the modal re-opens
  useEffect(() => {
    if (open) { setConfirm(""); setErr(null); setBusy(false); }
  }, [open]);

  async function submit() {
    if (!matches || busy) return;
    setBusy(true); setErr(null);
    try {
      const res = await apiDelete<{ agentName: string; cascade: { devicesUnbound: number; chatRowsDeleted: number; assessmentsDeleted: number } }>(`/agent/${agentId}`);
      onDeleted(res.agentName, res.cascade);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Delete failed.");
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open} onClose={onClose} title="Delete this client?" size="md"
      footer={
        <>
          <button onClick={onClose} className="btn-secondary">Cancel</button>
          <button onClick={submit} disabled={!matches || busy} className="btn-danger">
            {busy && <Loader2 size={14} className="animate-spin" />}
            <Trash2 size={14} /> Delete client
          </button>
        </>
      }
    >
      <div className="flex items-start gap-3 text-[14px] text-slate-deep leading-relaxed">
        <AlertTriangle size={18} className="text-risk-urgent shrink-0 mt-0.5" />
        <div>
          <p>
            <span className="font-medium">{agentName}</span> will be removed permanently from this dashboard, along with every conversation and risk assessment recorded for them.
          </p>
          <ul className="mt-3 space-y-1 text-[13px] text-slate-muted">
            <li>· Bound Watcher devices will be <span className="text-slate-deep">unbound</span> (the physical hardware row is kept so you can re-attach it).</li>
            <li>· All chat history rows will be <span className="text-risk-urgent">deleted</span>.</li>
            <li>· All medical assessments will be <span className="text-risk-urgent">deleted</span>.</li>
            <li>· Any admin scopes pointing at this client will be removed.</li>
          </ul>
          <p className="mt-4 text-[13px]">
            Type the client's name to confirm:
            <br />
            <span className="font-mono text-[13px] mt-2 text-slate-deep">{agentName}</span>
          </p>
          <input
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            className="input mt-2"
            placeholder={agentName}
            autoFocus
          />
        </div>
      </div>
      {err && (
        <div className="mt-4 text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
          {err}
        </div>
      )}
    </Modal>
  );
}

function ConfidenceRing({ value, level }: { value: number; level: string }) {
  const pct = Math.max(0, Math.min(1, value));
  const C = 2 * Math.PI * 18;
  const colorClass =
    level === "urgent"   ? "stroke-risk-urgent" :
    level === "elevated" ? "stroke-risk-elevated" :
    level === "moderate" ? "stroke-risk-moderate" : "stroke-risk-low";
  return (
    <div className="relative w-12 h-12">
      <svg width={48} height={48} viewBox="0 0 48 48">
        <circle cx={24} cy={24} r={18} stroke="rgba(56,67,81,0.12)" fill="none" strokeWidth={3} />
        <circle
          cx={24} cy={24} r={18} fill="none" strokeWidth={3} strokeLinecap="round"
          strokeDasharray={`${C * pct} ${C}`}
          transform="rotate(-90 24 24)"
          className={colorClass}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-[11px] font-medium num text-slate-deep">
        {Math.round(pct * 100)}
      </div>
    </div>
  );
}

export default function Page() {
  // We can't trust `useParams()` here. With output:'export' + try_files
  // fallback, Caddy serves the prerendered placeholder HTML for any
  // /patients/<unknown-id>/ URL. That placeholder was generated with
  // the dummy params {id: "_"} baked in, so useParams returns "_" on
  // first paint — and we'd hit /api/agent/_ → 404.
  //
  // Read the actual id from window.location.pathname instead. This matches
  // whatever the browser is currently showing, regardless of which static
  // HTML shell was served.
  const params = useParams<{ id: string }>();
  const [id, setId] = useState<string | null>(null);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const m = window.location.pathname.match(/\/patients\/([^/]+)\/?$/);
    const fromUrl = m?.[1];
    // Prefer the URL value; fall back to useParams in case routing changes.
    setId(fromUrl ?? (params?.id as string | undefined) ?? null);
  }, [params?.id]);

  return (
    <RequireAuth>
      <ToastProvider>
        <AppShell>
          {id && id !== "_" && <PatientDetailView id={id} />}
          {id === "_" && (
            <div className="min-h-[60vh] grid place-items-center text-slate-muted text-[12px] uppercase tracking-[0.12em]">
              loading client…
            </div>
          )}
        </AppShell>
      </ToastProvider>
    </RequireAuth>
  );
}
