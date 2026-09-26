"use client";

import { classNames } from "@/lib/format";
import { revelDiagnosticRows, type DiagnosticRow } from "@/lib/revelDiagnostics";
import type { RevelStatus } from "@/lib/revelStatus";

export function RevelDiagnosticsList({
  status,
  compact = false,
}: {
  status: RevelStatus;
  compact?: boolean;
}) {
  const rows = revelDiagnosticRows(status);
  return (
    <dl className={classNames("space-y-2 text-slate-deep", compact ? "text-[12px]" : "text-[13px]")}>
      {rows.map((row) => (
        <DiagnosticRowView key={row.label} row={row} />
      ))}
    </dl>
  );
}

function DiagnosticRowView({ row }: { row: DiagnosticRow }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-slate-muted shrink-0">{row.label}</dt>
      <dd className={classNames("text-right min-w-0 break-all", row.mono && "font-mono text-[12px]")}>
        {row.value}
      </dd>
    </div>
  );
}
