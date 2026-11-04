export const SAM_REVIEW_EMPTY = "No stored Sam conversations.";
export const SAM_REVIEW_MINIMUM_VOTES = 10;

/** Virtual Agent Control review. Client pages do not use this view. */
export function agentSamReviewPath(agentId: string): string {
  return `/agent/${agentId}/sam-review?view=agent`;
}

export type SamReviewTurn = {
  question: string;
  answer: string;
  followUp?: string | null;
  createdAt?: string | null;
};

export type SamReviewSession = {
  sessionId: string;
  createdAt?: string | null;
  contextKind: "library" | "account_device" | string;
  accountId?: string | null;
  registeredDeviceId?: string | null;
  nexusClientId?: string | null;
  answerCount: number;
  helpful: boolean | null;
  feedbackAt?: string | null;
  turns: SamReviewTurn[];
};

export type SamReviewSummary = {
  helpful: number;
  notHelpful: number;
  votes: number;
  percent: number | null;
};

export function samContextLabel(kind: string): string {
  return kind === "library" ? "Administrative" : "Account device";
}

export function samFeedbackLabel(helpful: boolean | null): "Yes" | "No" | "No vote" {
  if (helpful === true) return "Yes";
  if (helpful === false) return "No";
  return "No vote";
}

/** Percentage is withheld until 10 Yes or No votes. Skip is not a vote. */
export function samHelpfulnessSummary(helpful: number, notHelpful: number): SamReviewSummary {
  const votes = helpful + notHelpful;
  return {
    helpful,
    notHelpful,
    votes,
    percent: votes >= SAM_REVIEW_MINIMUM_VOTES ? Math.round((helpful * 100) / votes) : null,
  };
}
