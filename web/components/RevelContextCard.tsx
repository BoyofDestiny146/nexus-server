"use client";

import { revelContextCardModel } from "@/lib/revelStatus";
import type { RevelDiscussionContext } from "@/lib/revelStatus";

export function RevelContextCard({ context }: { context: RevelDiscussionContext }) {
  const card = revelContextCardModel(context);
  return (
    <div
      data-testid="revel-context-card"
      data-auto-trigger={context.autoTrigger ? "enabled" : "manual"}
      className="w-full max-w-[min(100%,28rem)] border border-slate-line/80 bg-bone-soft/50 rounded-card px-3.5 py-2.5"
    >
      <div className="text-[10px] uppercase tracking-[0.14em] text-slate-muted">
        {card.title}
      </div>
      <dl className="mt-2 space-y-1 text-[13px] text-slate-deep">
        {card.rows.map((row) => (
          <div key={row.label} className="flex items-baseline justify-between gap-3">
            <dt className="text-slate-muted shrink-0">{row.label}</dt>
            <dd
              className={
                row.label === "Tag"
                  ? "font-mono text-[12px] text-right min-w-0 break-all"
                  : row.label === "Display"
                    ? "text-right min-w-0 break-all"
                    : undefined
              }
            >
              {row.value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
