import type { SamReviewSession } from "./samConversationReview";

/** Center panel prompt when Ask Sam sessions exist but none is selected. */
export const SAM_CENTER_SELECT_PROMPT =
  "Select an Ask Sam Discussion in the left column to view questions and responses here.";

/** Poll interval while a Sam discussion is selected in the center panel. */
export const SAM_DISCUSSION_POLL_MS = 30_000;

/** Default agent identity label for Ask Sam turns in the center panel. */
export const SAM_DISCUSSION_AGENT_LABEL = "Sam";

/** Review for the client currently open. Sessions belong to that client id. */
export function askSamReviewPath(clientId: string): string | null {
  const id = clientId.trim();
  if (id === "") return null;
  return `/agent/${id}/sam-review`;
}

/** The section is shown only when this client has stored sessions of its own. */
export function askSamDiscussionsVisible(
  sessions: readonly { sessionId: string }[] | null | undefined,
): boolean {
  return (sessions?.length ?? 0) > 0;
}

/**
 * View Discussion: auto-select only when exactly one Sam session exists.
 * Multiple sessions leave selection clear so the left list is the picker.
 */
export function pickSamSessionForView(
  sessions: readonly { sessionId: string }[] | null | undefined,
): string | null {
  if (!sessions || sessions.length !== 1) return null;
  return sessions[0].sessionId;
}

/**
 * Stored Sam Review timestamps have no timezone. Format the clock time as
 * recorded so a browser in another zone does not shift it.
 */
export function samReviewTimestamp(value: string | null | undefined): string {
  if (!value || value.trim() === "") return "Time not recorded";
  const match = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/.exec(value.trim());
  if (!match) return value;
  const instant = new Date(Date.UTC(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
    Number(match[4]),
    Number(match[5]),
    Number(match[6] ?? 0),
  ));
  return instant.toLocaleString("en-US", {
    timeZone: "UTC",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

export function samDiscussionTurnCount(sessions: SamReviewSession[]): number {
  return sessions.reduce((sum, session) => sum + session.turns.length, 0);
}

export function samDiscussionSession(
  sessions: SamReviewSession[],
  sessionId: string,
): SamReviewSession | null {
  return sessions.find((session) => session.sessionId === sessionId) ?? null;
}
