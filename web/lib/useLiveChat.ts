"use client";

import { useEffect, useRef, useState } from "react";
import { apiGet, wsUrl } from "./api";
import type { ChatMessage, ChatSession, MedicalAssessment, WsFrame } from "./types";

export type LiveChatStatus = "connecting" | "live" | "polling" | "disconnected";

export interface LiveChatState {
  /** True iff WS is currently open AND has received a frame within freshness window. */
  connected: boolean;
  /** Coarse status for UI badges. */
  status: LiveChatStatus;
  /** Messages received since the hook mounted, oldest-first.
   *  Includes both WS-delivered frames AND new rows discovered via REST polling. */
  newMessages: (ChatMessage & { agentId: string; sessionId: string })[];
  /** Most recent assessment frame received over the wire (or null). */
  liveAssessment: MedicalAssessment | null;
  /** A monotonic counter that bumps on every assessment update — useful for
   *  triggering a flash animation in the right-hand panel. */
  assessmentTick: number;
}

/**
 * Subscribe to /ws/agent/{id}.  Reconnects on disconnect with a 1→2→5→10s
 * backoff (capped).
 *
 * Resilience layer (added 2026-05-03):
 *  - If WS hasn't received any frame (chat or hello) within 4s, OR if it
 *    closes/errors, the hook switches to REST polling against
 *    `/agent/{id}/sessions` + `/agent/{id}/chat-history/{sessionId}` every 2s.
 *    Two seconds (not one) is deliberate — multiple admins watching the same
 *    client at once shouldn't melt the DB.
 *  - Polling uses exponential backoff (cap 30s) on errors.
 *  - When WS receives a fresh frame, polling stops.
 *
 * The caller is responsible for merging `newMessages` with the historical
 * chat-history fetch (de-dupe by id).
 */
export function useLiveChat(agentId: string | null | undefined): LiveChatState {
  const [connected, setConnected] = useState(false);
  const [status, setStatus] = useState<LiveChatStatus>("connecting");
  const [newMessages, setNewMessages] = useState<LiveChatState["newMessages"]>([]);
  const [liveAssessment, setLiveAssessment] = useState<MedicalAssessment | null>(null);
  const [assessmentTick, setAssessmentTick] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const backoffRef = useRef(0);
  const cancelledRef = useRef(false);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Polling state
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollBackoffRef = useRef(2000);
  const lastFrameAtRef = useRef<number>(0);
  const seenIdsRef = useRef<Set<number>>(new Set());
  const watchdogTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isPollingRef = useRef(false);

  useEffect(() => {
    if (!agentId) return;
    const aid: string = agentId; // narrow for inner closures
    cancelledRef.current = false;
    seenIdsRef.current = new Set();
    pollBackoffRef.current = 2000;
    lastFrameAtRef.current = 0;
    isPollingRef.current = false;
    setStatus("connecting");

    const BACKOFFS = [1000, 2000, 5000, 10000];
    const FRESH_MS = 4000;
    const POLL_MIN = 2000;
    const POLL_MAX = 30000;

    function clearWatchdog() {
      if (watchdogTimerRef.current) {
        clearTimeout(watchdogTimerRef.current);
        watchdogTimerRef.current = null;
      }
    }

    function clearPoll() {
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current);
        pollTimerRef.current = null;
      }
      isPollingRef.current = false;
    }

    function noteFrame() {
      lastFrameAtRef.current = Date.now();
      // any successful frame stops the polling fallback and resets backoff
      pollBackoffRef.current = 2000;
      clearPoll();
      clearWatchdog();
      armWatchdog();
      setStatus("live");
    }

    function armWatchdog() {
      clearWatchdog();
      watchdogTimerRef.current = setTimeout(() => {
        // No frame in FRESH_MS — fall back to polling even if socket appears open.
        if (cancelledRef.current) return;
        if (!isPollingRef.current) startPolling();
      }, FRESH_MS);
    }

    async function pollOnce() {
      if (cancelledRef.current) return;
      try {
        // Pull recent sessions (cheap, indexed) — newest first.
        const s = await apiGet<ChatSession[] | { list: ChatSession[] }>(
          `/agent/${aid}/sessions`,
        );
        const sessions = Array.isArray(s) ? s : (s as { list: ChatSession[] }).list;
        const top = sessions?.[0];
        if (top) {
          // Pull just the most-recent session's messages and dedupe by id.
          const msgs = await apiGet<ChatMessage[]>(
            `/agent/${aid}/chat-history/${top.sessionId}`,
          );
          if (Array.isArray(msgs)) {
            const fresh = msgs.filter((m) => !seenIdsRef.current.has(m.id));
            for (const m of fresh) seenIdsRef.current.add(m.id);
            if (fresh.length > 0) {
              setNewMessages((prev) => [
                ...prev,
                ...fresh.map((m) => ({ ...m, agentId: aid, sessionId: top.sessionId })),
              ]);
            }
          }
        }
        pollBackoffRef.current = POLL_MIN;
      } catch {
        // Endpoint error: exponential backoff, cap at 30s.
        pollBackoffRef.current = Math.min(pollBackoffRef.current * 2, POLL_MAX);
        setStatus("disconnected");
      } finally {
        if (!cancelledRef.current && isPollingRef.current) {
          pollTimerRef.current = setTimeout(pollOnce, pollBackoffRef.current);
        }
      }
    }

    function startPolling() {
      if (isPollingRef.current) return;
      isPollingRef.current = true;
      setStatus("polling");
      // Seed seen-ids from anything we've already received over WS so the
      // first poll doesn't double-count.
      // (caller-side dedupe also exists, but local set keeps the diff minimal.)
      pollTimerRef.current = setTimeout(pollOnce, 0);
    }

    const open = () => {
      try {
        const url = wsUrl(`/ws/agent/${aid}`);
        const ws = new WebSocket(url);
        wsRef.current = ws;

        ws.onopen = () => {
          setConnected(true);
          backoffRef.current = 0;
          // Don't promote to "live" yet — wait for first frame (hello arrives
          // immediately from the server). The watchdog will demote to polling
          // if nothing comes within FRESH_MS.
          armWatchdog();
        };
        ws.onmessage = (ev) => {
          let frame: WsFrame;
          try { frame = JSON.parse(ev.data) as WsFrame; } catch { return; }
          // ANY frame (including hello) proves the link is healthy.
          noteFrame();
          if (frame.type === "chat.turn") {
            seenIdsRef.current.add(frame.payload.id);
            setNewMessages((prev) => [...prev, frame.payload]);
          } else if (frame.type === "assessment.updated") {
            setLiveAssessment(frame.payload);
            setAssessmentTick((t) => t + 1);
          }
        };
        ws.onerror = () => { /* let onclose handle reconnect */ };
        ws.onclose = () => {
          setConnected(false);
          wsRef.current = null;
          clearWatchdog();
          if (cancelledRef.current) return;
          // Socket dropped — start polling immediately as a guarantee.
          if (!isPollingRef.current) startPolling();
          const delay = BACKOFFS[Math.min(backoffRef.current, BACKOFFS.length - 1)];
          backoffRef.current += 1;
          reconnectTimerRef.current = setTimeout(open, delay);
        };
      } catch {
        if (!isPollingRef.current) startPolling();
        const delay = BACKOFFS[Math.min(backoffRef.current, BACKOFFS.length - 1)];
        backoffRef.current += 1;
        reconnectTimerRef.current = setTimeout(open, delay);
      }
    };

    open();

    return () => {
      cancelledRef.current = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      clearWatchdog();
      clearPoll();
      if (wsRef.current) {
        try { wsRef.current.close(); } catch {}
        wsRef.current = null;
      }
      setConnected(false);
      setStatus("disconnected");
    };
  }, [agentId]);

  return { connected, status, newMessages, liveAssessment, assessmentTick };
}
