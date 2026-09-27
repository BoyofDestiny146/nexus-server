"use client";

import { useEffect, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { apiGet, apiPost, ApiError } from "@/lib/api";
import { classNames, relativeTime } from "@/lib/format";
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
  isRoot,
  assessmentTick = 0,
}: {
  agentId: string;
  sessionId: string | null;
  isRoot: boolean;
  assessmentTick?: number;
}) {
  const [current, setCurrent] = useState<GenericAssessmentEnvelope | null>(null);
  const [payload, setPayload] = useState<SalesAssessmentPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [regenBusy, setRegenBusy] = useState(false);

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

  async function regenerate() {
    if (regenBusy || !isRoot) return;
    setRegenBusy(true);
    setError(null);
    const q = sessionId ? `?sessionId=${encodeURIComponent(sessionId)}` : "";
    try {
      const next = await apiPost<GenericAssessmentEnvelope>(
        `/agent/${agentId}/assessment/regenerate${q}`,
      );
      if (next && next.profileId === SALES_PRODUCT_ID) {
        setCurrent(next);
        setPayload(asSalesPayload(next.payload));
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Regenerate failed.");
    } finally {
      setRegenBusy(false);
    }
  }

  const lists = payload ? visibleSalesListSections(payload) : [];
  const showSummary = payload ? hasSalesSummary(payload) : false;

  return (
    <div data-testid="sales-product-panel">
      <div data-testid="current-assessment">
        <div className="kicker mb-3">Current assessment</div>

        {loading ? (
          <p className="text-[14px] text-slate-muted leading-relaxed">Loading assessment…</p>
        ) : error && !payload ? (
          <div className="text-[14px] text-slate-muted leading-relaxed">
            {error}{" "}
            {isRoot && (
              <button
                className="text-teal-deep hover:underline"
                onClick={regenerate}
                disabled={regenBusy}
              >
                Try again →
              </button>
            )}
          </div>
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
                className="mt-6"
                data-testid={`sales-section-${section.key}`}
              >
                <div className="kicker mb-2">{section.title}</div>
                <ul className="space-y-1.5 text-[13.5px] text-slate-deep leading-relaxed">
                  {payload[section.key].map((item, i) => (
                    <li key={i} className="flex gap-2">
                      <span className="text-slate-muted">·</span>
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}

            {showSummary && (
              <div className="mt-6" data-testid="sales-section-summary">
                <div className="kicker mb-2">Summary</div>
                <p className="text-[13.5px] text-slate-deep leading-relaxed">
                  {payload.summary}
                </p>
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
            No current assessment. {isRoot && (
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
    </div>
  );
}
