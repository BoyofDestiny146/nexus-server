"use client";

import type { RiskLevel } from "@/lib/types";
import { classNames } from "@/lib/format";

const COLORS: Record<RiskLevel, string> = {
  low:      "bg-risk-low text-risk-low",
  moderate: "bg-risk-moderate text-risk-moderate",
  elevated: "bg-risk-elevated text-risk-elevated",
  urgent:   "bg-risk-urgent text-risk-urgent",
};

const LABELS: Record<RiskLevel, string> = {
  low:      "Low",
  moderate: "Moderate",
  elevated: "Elevated",
  urgent:   "Urgent",
};

export function RiskDot({
  level,
  size = "md",
  pulse = false,
  showLabel = false,
}: {
  level: RiskLevel | null | undefined;
  size?: "sm" | "md" | "lg";
  pulse?: boolean;
  showLabel?: boolean;
}) {
  const dim = size === "sm" ? "w-2 h-2" : size === "lg" ? "w-3.5 h-3.5" : "w-2.5 h-2.5";
  const label = level ? LABELS[level] : "Not assessed";
  const colour = level ? COLORS[level].split(" ")[0] : "bg-slate-line";
  return (
    <span className="inline-flex items-center gap-2" aria-label={`Risk: ${label}`}>
      <span
        className={classNames(
          "inline-block rounded-full",
          dim,
          colour,
          pulse && "risk-pulse",
        )}
        title={label}
      />
      {showLabel && (
        <span className="text-[12px] tracking-tight text-slate-muted">{label}</span>
      )}
    </span>
  );
}

export function RiskBadge({ level }: { level: RiskLevel | null | undefined }) {
  const label = level ? LABELS[level] : "Not assessed";
  if (!level) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-chip text-[11px] tracking-tight bg-bone-soft border border-slate-line/70 text-slate-muted">
        <span className="w-1.5 h-1.5 rounded-full bg-slate-line" /> {label}
      </span>
    );
  }
  return (
    <span className={classNames(
      "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-chip text-[11px] tracking-tight border",
      level === "low"      && "bg-risk-low/10 border-risk-low/40 text-risk-low",
      level === "moderate" && "bg-risk-moderate/10 border-risk-moderate/40 text-risk-moderate",
      level === "elevated" && "bg-risk-elevated/10 border-risk-elevated/40 text-risk-elevated",
      level === "urgent"   && "bg-risk-urgent/10 border-risk-urgent/40 text-risk-urgent",
    )}>
      <span className={classNames("w-1.5 h-1.5 rounded-full", COLORS[level].split(" ")[0])} />
      {label}
    </span>
  );
}
