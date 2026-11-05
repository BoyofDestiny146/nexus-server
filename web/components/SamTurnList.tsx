import {
  samClientResponseLabel,
  samContextLabel,
  samFeedbackLabel,
  samFollowUpHeading,
  type SamReviewSession,
  type SamReviewTurn,
} from "@/lib/samConversationReview";
import { samReviewTimestamp } from "@/lib/askSamDiscussions";
import { virtualAgentTurnFollowUp } from "@/lib/virtualAgentBinding";

export function SamTurnList({
  session,
  agentLabel = "Sam",
}: {
  session: SamReviewSession;
  agentLabel?: string;
}) {
  return (
    <div className="space-y-4" data-testid="sam-turn-list">
      <div className="text-[13px] text-slate-muted">
        {samReviewTimestamp(session.createdAt)} · {samContextLabel(session.contextKind)}
      </div>
      <div className="text-[13px] text-slate-muted">
        {agentLabel} · {session.answerCount} completed{" "}
        {session.answerCount === 1 ? "answer" : "answers"} · Feedback{" "}
        {samFeedbackLabel(session.helpful)}
        {session.feedbackAt ? ` · ${samReviewTimestamp(session.feedbackAt)}` : ""}
      </div>
      <ol className="space-y-3">
        {session.turns.map((turn, index) => (
          <SamTurnRow
            key={`${session.sessionId}-${turn.id ?? index}`}
            turn={turn}
            agentLabel={agentLabel}
            testId="ask-sam-turn"
          />
        ))}
      </ol>
    </div>
  );
}

export function SamTurnRow({
  turn,
  agentLabel = "Sam",
  testId,
}: {
  turn: SamReviewTurn;
  agentLabel?: string;
  testId?: string;
}) {
  const followUp = virtualAgentTurnFollowUp(turn);
  const clientResponse = samClientResponseLabel(turn.followUpResponse);
  return (
    <li
      data-testid={testId}
      className="rounded-card border border-slate-line/70 bg-white/80 px-3 py-2.5"
    >
      <div className="text-[12px] text-slate-muted">{samReviewTimestamp(turn.createdAt)}</div>
      <div className="mt-1 text-[12px] text-slate-muted">Question</div>
      <div className="text-[14px] text-slate-deep leading-relaxed whitespace-pre-wrap">{turn.question}</div>
      <div className="mt-2 text-[12px] text-slate-muted">{agentLabel}</div>
      <div className="text-[14px] text-slate-deep leading-relaxed whitespace-pre-wrap">{turn.answer}</div>
      {followUp !== "" && (
        <div data-testid="sam-turn-follow-up">
          <div className="mt-2 text-[12px] text-slate-muted">{samFollowUpHeading(agentLabel)}</div>
          <div className="text-[14px] text-slate-deep leading-relaxed whitespace-pre-wrap">{followUp}</div>
        </div>
      )}
      {followUp !== "" && clientResponse !== null && (
        <div data-testid="sam-turn-client-response">
          <div className="mt-2 text-[12px] text-slate-muted">Client Response</div>
          <div className="text-[14px] text-slate-deep leading-relaxed">{clientResponse}</div>
        </div>
      )}
    </li>
  );
}
