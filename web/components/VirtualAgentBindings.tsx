"use client";

import { useEffect, useState } from "react";
import { Loader2, Plus } from "lucide-react";
import { apiDelete, apiGet, apiPost, ApiError } from "@/lib/api";
import { Modal } from "@/components/Modal";
import { relativeTime } from "@/lib/format";
import {
  EMPTY_AVAILABLE_VIRTUAL_AGENTS,
  EMPTY_VIRTUAL_AGENT_DISCUSSION,
  EMPTY_VIRTUAL_AGENTS,
  VIRTUAL_AGENTS_PATH,
  type BoundVirtualAgent,
  type VirtualAgentChoice,
  type VirtualAgentTurn,
  bindVirtualAgentPayload,
  clientVirtualAgentPath,
  clientVirtualAgentsPath,
  selectableVirtualAgents,
  virtualAgentLabel,
  virtualAgentSessionPath,
  virtualAgentSessionsPath,
  virtualAgentTurnFollowUp,
} from "@/lib/virtualAgentBinding";

interface SessionRow {
  sessionId: string;
  createdAt?: string | null;
  messageCount: number;
}

export function VirtualAgentBindings({
  clientId,
  /** When true, omit the Virtual Agents kicker, empty copy, and Attach button (parent hosts AGENTS). */
  embedded = false,
  attachOpen: attachOpenProp,
  onAttachOpenChange,
  onBoundChange,
}: {
  clientId: string;
  embedded?: boolean;
  attachOpen?: boolean;
  onAttachOpenChange?: (open: boolean) => void;
  onBoundChange?: (bound: BoundVirtualAgent[] | null) => void;
}) {
  const [bound, setBound] = useState<BoundVirtualAgent[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [attachOpenInternal, setAttachOpenInternal] = useState(false);
  const [discussion, setDiscussion] = useState<BoundVirtualAgent | null>(null);
  const [pendingUnbind, setPendingUnbind] = useState<BoundVirtualAgent | null>(null);

  const controlled = typeof attachOpenProp === "boolean";
  const attachOpen = controlled ? attachOpenProp : attachOpenInternal;

  function setAttachOpen(open: boolean) {
    if (!controlled) setAttachOpenInternal(open);
    onAttachOpenChange?.(open);
  }

  function applyBound(rows: BoundVirtualAgent[] | null) {
    setBound(rows);
    onBoundChange?.(rows);
  }

  function reload() {
    applyBound(null);
    apiGet<BoundVirtualAgent[]>(clientVirtualAgentsPath(clientId))
      .then((rows) => {
        applyBound(rows ?? []);
        setLoadErr(null);
      })
      .catch((error) => {
        applyBound([]);
        setLoadErr(error instanceof ApiError ? error.message : "Could not load virtual agents.");
      });
  }

  useEffect(() => {
    let cancelled = false;
    apiGet<BoundVirtualAgent[]>(clientVirtualAgentsPath(clientId))
      .then((rows) => {
        if (!cancelled) applyBound(rows ?? []);
      })
      .catch((error) => {
        if (cancelled) return;
        applyBound([]);
        setLoadErr(error instanceof ApiError ? error.message : "Could not load virtual agents.");
      });
    return () => {
      cancelled = true;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps -- reload on clientId only
  }, [clientId]);

  return (
    <div>
      {!embedded && <div className="kicker px-2 mb-1.5">Virtual Agents</div>}
      {bound === null && <p className="px-2 py-1.5 text-[12px] text-slate-muted">Loading…</p>}
      {loadErr && (
        <p className="px-2 py-1.5 text-[12px] text-risk-urgent leading-relaxed">{loadErr}</p>
      )}
      {!embedded && bound && bound.length === 0 && !loadErr && (
        <p className="px-2 py-1.5 text-[12px] text-slate-muted leading-relaxed">{EMPTY_VIRTUAL_AGENTS}</p>
      )}
      {bound && bound.length > 0 && (
        <ul className={embedded && bound.length > 0 ? "space-y-2 mt-2" : "space-y-2"}>
          {bound.map((agent) => (
            <li key={agent.virtualAgentId} className="rounded-card border border-slate-line/70 bg-white/80 px-2.5 py-2">
              <div className="text-[13px] tracking-tight text-slate-deep leading-snug">{virtualAgentLabel(agent)}</div>
              <div className="mt-0.5 text-[11px] uppercase tracking-[0.12em] text-slate-muted">Virtual</div>
              {agent.agentName && agent.botName && agent.agentName !== agent.botName ? (
                <div className="mt-0.5 text-[11px] text-slate-muted truncate">{agent.agentName}</div>
              ) : null}
              <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
                <button type="button" className="text-[12px] text-teal-deep hover:underline" onClick={() => setDiscussion(agent)}>
                  View Discussion
                </button>
                <button type="button" className="text-[12px] text-slate-muted hover:text-slate-deep" onClick={() => setPendingUnbind(agent)}>
                  Unbind
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {!embedded && (
        <button
          type="button"
          onClick={() => setAttachOpen(true)}
          className="mt-2 w-full flex items-center gap-2.5 px-2 py-2 rounded-card text-[13px] tracking-tight text-slate-deep hover:bg-white/90 transition"
        >
          <Plus size={14} className="text-slate-muted shrink-0" />
          Attach Virtual Agent
        </button>
      )}
      <AttachVirtualAgentModal
        open={attachOpen}
        clientId={clientId}
        boundIds={(bound ?? []).map((agent) => agent.virtualAgentId)}
        onClose={() => setAttachOpen(false)}
        onAttached={() => {
          setAttachOpen(false);
          reload();
        }}
      />
      <VirtualAgentDiscussionModal agent={discussion} clientId={clientId} onClose={() => setDiscussion(null)} />
      <UnbindVirtualAgentModal
        agent={pendingUnbind}
        clientId={clientId}
        onClose={() => setPendingUnbind(null)}
        onRemoved={() => {
          setPendingUnbind(null);
          reload();
        }}
      />
    </div>
  );
}

export function AttachVirtualAgentModal({
  open,
  clientId,
  boundIds,
  onClose,
  onAttached,
}: {
  open: boolean;
  clientId: string;
  boundIds: string[];
  onClose: () => void;
  onAttached: () => void;
}) {
  const [available, setAvailable] = useState<VirtualAgentChoice[] | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setSelectedId("");
    setBusy(false);
    setErr(null);
    setAvailable(null);
    let cancelled = false;
    apiGet<VirtualAgentChoice[]>(VIRTUAL_AGENTS_PATH)
      .then((rows) => {
        if (!cancelled) setAvailable(rows ?? []);
      })
      .catch((error) => {
        if (cancelled) return;
        setAvailable([]);
        setErr(error instanceof ApiError ? error.message : "Could not load virtual agents.");
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const choices = selectableVirtualAgents(available ?? [], clientId, boundIds);
  const loading = available === null;

  async function submit() {
    if (!selectedId || busy) return;
    setBusy(true);
    setErr(null);
    try {
      await apiPost(clientVirtualAgentsPath(clientId), bindVirtualAgentPayload(selectedId));
      onAttached();
    } catch (error) {
      setErr(error instanceof ApiError ? error.message : "Could not attach this virtual agent.");
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Attach Virtual Agent"
      size="md"
      footer={
        <>
          <button type="button" onClick={onClose} className="btn-secondary">Cancel</button>
          <button type="button" onClick={submit} disabled={!selectedId || busy || loading} className="btn-primary">
            {busy && <Loader2 size={14} className="animate-spin" />}
            Attach Virtual Agent
          </button>
        </>
      }
    >
      <div className="space-y-4">
        {loading && <div className="text-[13px] text-slate-muted">Loading virtual agents…</div>}
        {!loading && choices.length === 0 && !err && (
          <div className="text-[13.5px] text-slate-deep leading-relaxed border border-slate-line/80 bg-bone-soft/60 rounded-card px-3 py-3">
            {EMPTY_AVAILABLE_VIRTUAL_AGENTS}
          </div>
        )}
        {choices.length > 0 && (
          <div>
            <label htmlFor="attach-virtual-agent" className="label">Select virtual agent</label>
            <select
              id="attach-virtual-agent"
              className="input"
              value={selectedId}
              onChange={(event) => setSelectedId(event.target.value)}
            >
              <option value="">Select a virtual agent</option>
              {choices.map((agent) => (
                <option key={agent.id} value={agent.id}>{virtualAgentLabel(agent)}</option>
              ))}
            </select>
          </div>
        )}
        {err && (
          <div className="text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">{err}</div>
        )}
      </div>
    </Modal>
  );
}

function VirtualAgentDiscussionModal({
  agent,
  clientId,
  onClose,
}: {
  agent: BoundVirtualAgent | null;
  clientId: string;
  onClose: () => void;
}) {
  const [sessions, setSessions] = useState<SessionRow[] | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<VirtualAgentTurn[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!agent) return;
    setSessions(null);
    setSessionId(null);
    setTurns(null);
    setErr(null);
    let cancelled = false;
    apiGet<{ list: SessionRow[] }>(virtualAgentSessionsPath(clientId, agent.virtualAgentId))
      .then((body) => {
        if (cancelled) return;
        const list = body?.list ?? [];
        setSessions(list);
        setSessionId(list[0]?.sessionId ?? null);
      })
      .catch((error) => {
        if (cancelled) return;
        setSessions([]);
        setErr(error instanceof ApiError ? error.message : "Could not load the discussion.");
      });
    return () => {
      cancelled = true;
    };
  }, [agent, clientId]);

  useEffect(() => {
    if (!agent || !sessionId) {
      setTurns(null);
      return;
    }
    let cancelled = false;
    setTurns(null);
    apiGet<VirtualAgentTurn[]>(virtualAgentSessionPath(clientId, agent.virtualAgentId, sessionId))
      .then((rows) => {
        if (!cancelled) setTurns(rows ?? []);
      })
      .catch((error) => {
        if (cancelled) return;
        setTurns([]);
        setErr(error instanceof ApiError ? error.message : "Could not load the discussion.");
      });
    return () => {
      cancelled = true;
    };
  }, [agent, clientId, sessionId]);

  const empty = sessions !== null && sessions.length === 0 && !err;

  return (
    <Modal open={agent !== null} onClose={onClose} title="View Discussion" size="lg">
      <div className="space-y-4">
        <div className="text-[13px] text-slate-muted">{agent ? virtualAgentLabel(agent) : ""}</div>
        {sessions === null && <div className="text-[13px] text-slate-muted">Loading…</div>}
        {empty && (
          <div className="text-[13.5px] text-slate-deep leading-relaxed border border-slate-line/80 bg-bone-soft/60 rounded-card px-3 py-3">
            {EMPTY_VIRTUAL_AGENT_DISCUSSION}
          </div>
        )}
        {sessions && sessions.length > 1 && (
          <div>
            <label htmlFor="virtual-agent-session" className="label">Session</label>
            <select
              id="virtual-agent-session"
              className="input"
              value={sessionId ?? ""}
              onChange={(event) => setSessionId(event.target.value)}
            >
              {sessions.map((session) => (
                <option key={session.sessionId} value={session.sessionId}>
                  {relativeTime(session.createdAt)} · {session.messageCount}
                </option>
              ))}
            </select>
          </div>
        )}
        {turns && turns.length > 0 && (
          <ol className="space-y-3">
            {turns.map((turn) => {
              const followUp = virtualAgentTurnFollowUp(turn);
              return (
                <li key={turn.id} className="rounded-card border border-slate-line/70 bg-white/80 px-3 py-2.5">
                  <div className="text-[12px] text-slate-muted">Question</div>
                  <div className="text-[14px] text-slate-deep leading-relaxed">{turn.question}</div>
                  <div className="mt-2 text-[12px] text-slate-muted">Sam</div>
                  <div className="text-[14px] text-slate-deep leading-relaxed">{turn.answer}</div>
                  {followUp !== "" && (
                    <>
                      <div className="mt-2 text-[12px] text-slate-muted">Follow-up</div>
                      <div className="text-[14px] text-slate-deep leading-relaxed">{followUp}</div>
                    </>
                  )}
                </li>
              );
            })}
          </ol>
        )}
        {err && (
          <div className="text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">{err}</div>
        )}
      </div>
    </Modal>
  );
}

function UnbindVirtualAgentModal({
  agent,
  clientId,
  onClose,
  onRemoved,
}: {
  agent: BoundVirtualAgent | null;
  clientId: string;
  onClose: () => void;
  onRemoved: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (agent) {
      setBusy(false);
      setErr(null);
    }
  }, [agent]);

  async function submit() {
    if (!agent || busy) return;
    setBusy(true);
    setErr(null);
    try {
      await apiDelete(clientVirtualAgentPath(clientId, agent.virtualAgentId));
      onRemoved();
    } catch (error) {
      setErr(error instanceof ApiError ? error.message : "Could not unbind this virtual agent.");
      setBusy(false);
    }
  }

  return (
    <Modal
      open={agent !== null}
      onClose={onClose}
      title="Unbind Virtual Agent"
      size="sm"
      footer={
        <>
          <button type="button" onClick={onClose} className="btn-secondary">Cancel</button>
          <button type="button" onClick={submit} disabled={busy} className="btn-danger">
            {busy && <Loader2 size={14} className="animate-spin" />}
            Unbind
          </button>
        </>
      }
    >
      <div className="text-[14px] text-slate-deep leading-relaxed">
        {agent ? `${virtualAgentLabel(agent)} will be removed from this client. Stored discussion remains available if the agent is attached again.` : ""}
        {err && (
          <div className="mt-3 text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">{err}</div>
        )}
      </div>
    </Modal>
  );
}
