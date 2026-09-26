"use client";

import { classNames, shortTime } from "@/lib/format";
import {
  revelDisplayName,
  revelEventCardModel,
  revelResultPresentation,
  type RevelTimeline,
} from "@/lib/revelTimeline";

export function RevelDisplayEvent({
  event,
  timestamp,
}: {
  event: RevelTimeline;
  timestamp?: string | null;
}) {
  const card = revelEventCardModel(event, timestamp);
  const presentation = revelResultPresentation(event);
  const when = shortTime(card.timestamp);
  const display = revelDisplayName(event);
  return (
    <div
      data-testid="revel-display-event"
      data-result={presentation.code}
      data-event-type={card.type}
      className="w-full max-w-[min(100%,28rem)] border border-slate-line/80 bg-bone-soft/50 rounded-card px-3.5 py-2.5"
    >
      <div className="text-[10px] uppercase tracking-[0.14em] text-slate-muted">
        Display event
      </div>
      <dl className="mt-2 space-y-1 text-[13px] text-slate-deep">
        {card.tag ? (
          <div className="flex items-baseline justify-between gap-3">
            <dt className="text-slate-muted shrink-0">Revel Tag</dt>
            <dd className="font-mono text-[12px] text-right min-w-0 break-all">{card.tag}</dd>
          </div>
        ) : null}
        {display ? (
          <div className="flex items-baseline justify-between gap-3">
            <dt className="text-slate-muted shrink-0">Display</dt>
            <dd className="text-right min-w-0 break-all">{display}</dd>
          </div>
        ) : null}
      </dl>
      <div className="mt-2.5 flex items-center justify-between gap-3 text-[11px] uppercase tracking-[0.12em]">
        <span
          className={classNames(
            "inline-flex items-center gap-1.5",
            presentation.code === "displayed"
              ? "text-teal-deep"
              : presentation.code === "failed"
                ? "text-risk-urgent"
                : "text-slate-muted",
          )}
        >
          <span aria-hidden="true">{presentation.code === "displayed" ? "✓" : "○"}</span>
          {presentation.label}
        </span>
        {when ? <span className="text-slate-muted num normal-case tracking-tight">{when}</span> : null}
      </div>
    </div>
  );
}
