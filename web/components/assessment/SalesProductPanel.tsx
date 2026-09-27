"use client";

import { useEffect, useState } from "react";
import { apiGet, ApiError } from "@/lib/api";
import { classNames, longTime, relativeTime } from "@/lib/format";
import type { GenericAssessmentEnvelope, SalesAssessmentPayload } from "@/lib/types";
import { SALES_PRODUCT_ID } from "@/lib/assessmentProfiles";
import {
  asSalesPayload,
  hasSalesSummary,
  interestLevelLabel,
  visibleSalesListSections,
} from "@/lib/salesAssessment";

function interestClass(level: SalesAssessmentPayload["interestLevel"]): string {
  if (level === "high") return "text-teal-deep";
  if (level === "medium") return "text-slate-deep";
  return "text-slate-muted";
}

export function SalesProductPanel({
  agentId,
  sessionId,
  assessmentTick = 0,
  lastAssessmentAt,
}: {
  agentId: string;
  sessionId: string | null;
  assessmentTick?: number;
  lastAssessmentAt?: string | null;
}) {
  const [current, setCurrent] = useState<GenericAssessmentEnvelope | null>(null);
  const [payload, setPayload] = useState<SalesAssessmentPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (assessmentTick === 0) setLoading(true);
      setError(null);
      const q = sessionId ? `?sessionId=${encodeURIComponent(sessionId)}` : "";
      try {
        const data = await apiGet<GenericAssessmentEnvelope | null>(
          `/agent/${agentId}/assessment/current${q}`,
        );
        if (cancelled) return;
        if (data && data.profileId === SALES_PRODUCT_ID) {
          setCurrent(data);
          setPayload(asSalesPayload(data.payload));
        } else {
          setCurrent(null);
          setPayload(null);
        }
      } catch (e) {
        if (cancelled) return;
        setCurrent(null);
        setPayload(null);
        setError(e instanceof ApiError ? e.message : "Could not load sales assessment.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [agentId, sessionId, assessmentTick]);

  const lists = payload ? visibleSalesListSections(payload) : [];
  const showSummary = payload ? hasSalesSummary(payload) : false;
  const lastAt = current?.generatedAt ?? lastAssessmentAt ?? null;

  return (
    <div data-testid="sales-product-panel" className="min-w-0">
      <div data-testid="latest-assessment">
        <div className="kicker mb-3">Latest Assessment</div>

        {loading ? (
          <p className="text-[14px] text-slate-muted leading-relaxed">Loading assessment…</p>
        ) : error && !payload ? (
          <div className="text-[14px] text-slate-muted leading-relaxed">{error}</div>
        ) : payload && current ? (
          <>
            <div data-testid="sales-interest-level">
              <div className="kicker mb-2">Interest Level</div>
              <div
                className={classNames(
                  "display-2 leading-none",
                  interestClass(payload.interestLevel),
                )}
              >
                {interestLevelLabel(payload.interestLevel)}
              </div>
            </div>
            <div className="text-[12px] tracking-tight text-slate-muted num mt-2">
              {current.generatedAt ? relativeTime(current.generatedAt) : "just now"}
              {" · "}
              {current.sourceMsgCount}{" "}
              {current.sourceMsgCount === 1 ? "message" : "messages"}
            </div>

            {lists.map((section) => (
              <div
                key={section.key}
                className="mt-6 min-w-0"
                data-testid={`sales-section-${section.key}`}
              >
                <div className="kicker mb-2">{section.title}</div>
                <ul className="space-y-1.5 text-[13.5px] text-slate-deep leading-relaxed">
                  {payload[section.key].map((item, i) => (
                    <li key={i} className="flex gap-2 min-w-0">
                      <span className="text-slate-muted shrink-0">·</span>
                      <span className="min-w-0 break-words">{item}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}

            {showSummary && (
              <div className="mt-6 min-w-0" data-testid="sales-section-summary">
                <div className="kicker mb-2">Summary</div>
                <p className="text-[13.5px] text-slate-deep leading-relaxed break-words">
                  {payload.summary}
                </p>
              </div>
            )}

            <LastAssessmentStamp value={lastAt} />
          </>
        ) : (
          <>
            <div className="text-[14px] text-slate-muted leading-relaxed">
              No current assessment.
            </div>
            <LastAssessmentStamp value={lastAt} />
          </>
        )}
      </div>
    </div>
  );
}

function LastAssessmentStamp({ value }: { value: string | null }) {
  return (
    <div className="mt-6 min-w-0">
      <div className="kicker mb-1">Last Assessment</div>
      <div className="text-slate-deep num text-[13px] break-words" data-testid="assessment-last-at">
        {value ? longTime(value) : "—"}
      </div>
    </div>
  );
}
