"use client";

import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import { Modal } from "@/components/Modal";
import {
  askSamDiscussionsVisible,
  askSamReviewPath,
  samDiscussionSession,
  samReviewTimestamp,
} from "@/lib/askSamDiscussions";
import {
  samContextLabel,
  samFeedbackLabel,
  type SamReviewSession,
  type SamReviewSummary,
} from "@/lib/samConversationReview";
import { virtualAgentTurnFollowUp } from "@/lib/virtualAgentBinding";
import { classNames } from "@/lib/format";

export function AskSamDiscussions({ clientId }: { clientId: string }) {
  const path = askSamReviewPath(clientId);
  const [sessions, setSessions] = useState<SamReviewSession[] | null>(null);
  const [summary, setSummary] = useState<SamReviewSummary | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    setSessions(null);
    setSummary(null);
    setSelectedId(null);
    apiGet<{ summary: SamReviewSummary; sessions: SamReviewSession[] }>(path)
      .then((review) => {
        if (cancelled) return;
        setSummary(review.summary);
        setSessions(review.sessions ?? []);
      })
      .catch(() => {
        if (cancelled) return;
        setSessions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  if (!path || !askSamDiscussionsVisible(sessions)) return null;

  const selected = selectedId && sessions ? samDiscussionSession(sessions, selectedId) : null;

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
            const active = session.sessionId === selectedId;
            return (
              <li key={session.sessionId}>
                <button
                  type="button"
                  data-testid="ask-sam-session"
                  onClick={() => setSelectedId(session.sessionId)}
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
      <Modal
        open={selected !== null}
        onClose={() => setSelectedId(null)}
        title="Ask Sam Discussions"
        size="lg"
      >
        {selected && (
          <div className="space-y-4">
            <div className="text-[13px] text-slate-muted">
              {samReviewTimestamp(selected.createdAt)} · {samContextLabel(selected.contextKind)}
            </div>
            <div className="text-[13px] text-slate-muted">
              {selected.answerCount} completed {selected.answerCount === 1 ? "answer" : "answers"} · Feedback {samFeedbackLabel(selected.helpful)}
              {selected.feedbackAt ? ` · ${samReviewTimestamp(selected.feedbackAt)}` : ""}
            </div>
            <ol className="space-y-3">
              {selected.turns.map((turn, index) => {
                const followUp = virtualAgentTurnFollowUp(turn);
                return (
                  <li
                    key={`${selected.sessionId}-${index}`}
                    data-testid="ask-sam-turn"
                    className="rounded-card border border-slate-line/70 bg-white/80 px-3 py-2.5"
                  >
                    <div className="text-[12px] text-slate-muted">
                      {samReviewTimestamp(turn.createdAt)}
                    </div>
                    <div className="mt-1 text-[12px] text-slate-muted">Question</div>
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
          </div>
        )}
      </Modal>
    </div>
  );
}
