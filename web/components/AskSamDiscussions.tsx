"use client";

import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import {
  askSamDiscussionsVisible,
  askSamReviewPath,
  SAM_DISCUSSION_POLL_MS,
  samReviewTimestamp,
} from "@/lib/askSamDiscussions";
import {
  samContextLabel,
  samFeedbackLabel,
  type SamReviewSession,
  type SamReviewSummary,
} from "@/lib/samConversationReview";
import { classNames } from "@/lib/format";

export function AskSamDiscussions({
  clientId,
  selectedSessionId,
  onSelectSession,
  onReviewChange,
  pollWhileSelected = false,
}: {
  clientId: string;
  selectedSessionId: string | null;
  onSelectSession: (sessionId: string) => void;
  onReviewChange?: (review: {
    summary: SamReviewSummary | null;
    sessions: SamReviewSession[];
  }) => void;
  /** Lightweight sam-review refetch while a Sam session is selected. */
  pollWhileSelected?: boolean;
}) {
  const path = askSamReviewPath(clientId);
  const [sessions, setSessions] = useState<SamReviewSession[] | null>(null);
  const [summary, setSummary] = useState<SamReviewSummary | null>(null);

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    setSessions(null);
    setSummary(null);
    onReviewChange?.({ summary: null, sessions: [] });

    apiGet<{ summary: SamReviewSummary; sessions: SamReviewSession[] }>(path)
      .then((review) => {
        if (cancelled) return;
        const nextSessions = review.sessions ?? [];
        setSummary(review.summary);
        setSessions(nextSessions);
        onReviewChange?.({ summary: review.summary, sessions: nextSessions });
      })
      .catch(() => {
        if (cancelled) return;
        setSessions([]);
        onReviewChange?.({ summary: null, sessions: [] });
      });
    return () => {
      cancelled = true;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps -- reload when the client path changes
  }, [path]);

  useEffect(() => {
    if (!path || !pollWhileSelected || !selectedSessionId) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      apiGet<{ summary: SamReviewSummary; sessions: SamReviewSession[] }>(path)
        .then((review) => {
          if (cancelled) return;
          const nextSessions = review.sessions ?? [];
          setSummary(review.summary);
          setSessions(nextSessions);
          onReviewChange?.({ summary: review.summary, sessions: nextSessions });
        })
        .catch(() => { /* keep last successful review */ });
    }, SAM_DISCUSSION_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps -- poll only while a Sam session is selected
  }, [path, pollWhileSelected, selectedSessionId]);

  if (!path || !askSamDiscussionsVisible(sessions)) return null;

  return (
    <div className="px-3 pt-4 pb-3 border-b border-slate-line/70" data-testid="ask-sam-discussions">
      <div className="flex items-baseline justify-between gap-2">
        <div className="kicker">Ask Sam Discussions</div>
        <span className="text-[11px] tracking-tight text-slate-muted num">
          {sessions?.length ?? 0}
        </span>
      </div>
      {summary && (
        <p className="px-2 pt-2 text-[12px] text-slate-muted leading-relaxed">
          Helpful {summary.helpful} · Not helpful {summary.notHelpful}
          {summary.percent !== null ? ` · ${summary.percent}% helpful` : ""}
        </p>
      )}
      <div className="px-2 pb-1">
        <ul className="space-y-0.5">
          {sessions?.map((session) => {
            const active = session.sessionId === selectedSessionId;
            return (
              <li key={session.sessionId}>
                <button
                  type="button"
                  data-testid="ask-sam-session"
                  onClick={() => onSelectSession(session.sessionId)}
                  className={classNames(
                    "w-full text-left px-2.5 py-2 rounded-card border transition",
                    active
                      ? "bg-white border-slate-line text-slate-deep"
                      : "border-transparent text-slate hover:bg-white/70",
                  )}
                >
                  <div className="flex items-baseline justify-between gap-2 text-[13px] tracking-tight">
                    <span className={active ? "text-slate-deep" : ""}>
                      {samReviewTimestamp(session.createdAt)}
                    </span>
                    <span className="text-[11px] text-slate-muted num shrink-0">
                      {session.answerCount}
                    </span>
                  </div>
                  <div className="mt-0.5 text-[11px] text-slate-muted truncate">
                    {samContextLabel(session.contextKind)} · Feedback {samFeedbackLabel(session.helpful)}
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
