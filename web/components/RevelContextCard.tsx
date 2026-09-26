"use client";

import type { RevelDiscussionContext } from "@/lib/revelStatus";

export function RevelContextCard({ context }: { context: RevelDiscussionContext }) {
  return (
    <div
      data-testid="revel-context-card"
      data-auto-trigger={context.autoTrigger ? "enabled" : "manual"}
      className="w-full max-w-[min(100%,28rem)] border border-slate-line/80 bg-bone-soft/50 rounded-card px-3.5 py-2.5"
    >
      <div className="text-[10px] uppercase tracking-[0.14em] text-slate-muted">
        Revel context
      </div>
      <dl className="mt-2 space-y-1 text-[13px] text-slate-deep">
        <div className="flex items-baseline justify-between gap-3">
          <dt className="text-slate-muted shrink-0">Tag</dt>
          <dd className="font-mono text-[12px] text-right min-w-0 break-all">{context.tag}</dd>
        </div>
        <div className="flex items-baseline justify-between gap-3">
          <dt className="text-slate-muted shrink-0">Auto Trigger</dt>
          <dd>{context.autoTriggerLabel}</dd>
        </div>
        {context.display ? (
          <div className="flex items-baseline justify-between gap-3">
            <dt className="text-slate-muted shrink-0">Display</dt>
            <dd className="text-right min-w-0 break-all">{context.display}</dd>
          </div>
        ) : null}
      </dl>
    </div>
  );
}
