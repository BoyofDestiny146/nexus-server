"use client";

import type { MedicalAssessment, RiskLevel } from "@/lib/types";
import { longTime, relativeTime } from "@/lib/format";

export function CareWellnessPanel({
  latest,
  lastAssessmentAt,
}: {
  latest: MedicalAssessment | null;
  lastAssessmentAt?: string | null;
}) {
  const lastAt = latest?.generatedAt ?? lastAssessmentAt ?? null;

  return (
    <div data-testid="care-wellness-panel" className="min-w-0">
      <div data-testid="latest-assessment">
        <div className="kicker mb-3">Latest Assessment</div>
        {latest ? (
          <>
            <div className="flex items-center gap-3 min-w-0">
              <div className="display-2 text-slate-deep capitalize leading-none truncate">
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
              <div className="mt-6 min-w-0">
                <div className="kicker mb-2">Concerns</div>
                <ul className="space-y-1.5 text-[13.5px] text-slate-deep leading-relaxed">
                  {latest.concerns.map((c, i) => (
                    <li key={i} className="flex gap-2 min-w-0">
                      <span className="text-slate-muted shrink-0">·</span>
                      <span className="min-w-0 break-words">{c}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {latest.recommendations.length > 0 && (
              <div className="mt-6 min-w-0">
                <div className="kicker mb-2">Recommendations</div>
                <ul className="space-y-1.5 text-[13.5px] text-slate-deep leading-relaxed">
                  {latest.recommendations.map((r, i) => (
                    <li key={i} className="flex gap-2 min-w-0">
                      <span className="text-teal shrink-0">→</span>
                      <span className="min-w-0 break-words">{r}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {latest.concerns.length === 0 && latest.recommendations.length === 0 && (
              <p className="mt-6 text-[12px] text-slate-muted leading-relaxed">
                No current observations
              </p>
            )}

            <LastAssessmentStamp value={lastAt} />
          </>
        ) : (
          <>
            <div className="text-[14px] text-slate-muted leading-relaxed">
              No assessment yet.
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

function ConfidenceRing({ value, level }: { value: number; level: RiskLevel | string }) {
  const pct = Math.max(0, Math.min(1, value));
  const C = 2 * Math.PI * 18;
  const colorClass =
    level === "urgent"   ? "stroke-risk-urgent" :
    level === "elevated" ? "stroke-risk-elevated" :
    level === "moderate" ? "stroke-risk-moderate" : "stroke-risk-low";
  return (
    <div className="relative w-12 h-12 shrink-0">
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
