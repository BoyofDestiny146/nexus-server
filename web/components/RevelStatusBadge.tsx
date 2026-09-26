"use client";

import { Loader2 } from "lucide-react";
import { classNames } from "@/lib/format";
import {
  revelBadgeLabel,
  revelStatusTone,
  type RevelStatus,
} from "@/lib/revelStatus";

/** Presentational header chip. Parent owns Revel status fetching. */
export function RevelStatusBadge({
  status,
  loading = false,
  error = null,
}: {
  status: RevelStatus | null;
  loading?: boolean;
  error?: string | null;
}) {
  const tone = revelStatusTone(status?.mode);
  const label = loading
    ? "Revel…"
    : error || !status
      ? "Revel status unavailable"
      : revelBadgeLabel(status);
  const dot =
    tone === "enabled" ? "bg-teal" : tone === "manual" ? "bg-risk-moderate" : "bg-slate-line";
  const text =
    tone === "enabled"
      ? "text-teal-deep"
      : tone === "manual"
        ? "text-risk-moderate"
        : "text-slate-muted";

  return (
    <span
      title={label}
      aria-label={`Revel status. ${label}`}
      className={classNames(
        "inline-flex items-center gap-1.5 max-w-[min(100%,22rem)] sm:max-w-[28rem] rounded-chip border border-slate-line/80 bg-white/80 px-2 py-1 text-left",
        text,
      )}
    >
      {loading ? (
        <Loader2 size={10} className="animate-spin shrink-0 text-slate-muted" />
      ) : (
        <span className={classNames("w-1.5 h-1.5 rounded-full shrink-0", dot)} aria-hidden />
      )}
      <span className="truncate text-[10px] uppercase tracking-[0.12em]">{label}</span>
    </span>
  );
}
