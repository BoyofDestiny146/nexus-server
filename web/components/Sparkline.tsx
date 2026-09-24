"use client";

import type { MedicalAssessment, RiskLevel } from "@/lib/types";

const RISK_VALUE: Record<RiskLevel, number> = {
  low: 0.15,
  moderate: 0.45,
  elevated: 0.7,
  urgent: 0.95,
};
const RISK_COLOR: Record<RiskLevel, string> = {
  low:      "#88A89C",
  moderate: "#D2A14B",
  elevated: "#C26446",
  urgent:   "#A8392F",
};

/**
 * Tiny SVG sparkline.  No chart library — a 14-day risk trend rarely needs
 * the weight of D3.  Each datum is positioned by date so missing days
 * compress the line, which is the right behavior for clinicians.
 */
export function Sparkline({
  data,
  days = 14,
  width = 280,
  height = 56,
}: {
  data: MedicalAssessment[];
  days?: number;
  width?: number;
  height?: number;
}) {
  if (!data || data.length === 0) {
    return (
      <div
        className="flex items-center justify-center text-[11px] uppercase tracking-[0.12em] text-slate-muted/70"
        style={{ width, height }}
      >
        no recent history
      </div>
    );
  }

  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const start = today.getTime() - (days - 1) * 86_400_000;

  const PAD_X = 4;
  const PAD_Y = 6;
  const W = width - PAD_X * 2;
  const H = height - PAD_Y * 2;

  const points = data
    .map((a) => {
      const t = new Date(a.forDate).getTime();
      const x = PAD_X + (W * (t - start)) / ((days - 1) * 86_400_000);
      const v = RISK_VALUE[a.riskLevel] ?? 0.5;
      const y = PAD_Y + H * (1 - v);
      return { x, y, level: a.riskLevel };
    })
    .filter((p) => p.x >= PAD_X - 0.5 && p.x <= width - PAD_X + 0.5);

  if (points.length === 0) {
    return (
      <div
        className="flex items-center justify-center text-[11px] uppercase tracking-[0.12em] text-slate-muted/70"
        style={{ width, height }}
      >
        no data in window
      </div>
    );
  }

  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`)
    .join(" ");
  const last = points[points.length - 1];
  const lastColor = RISK_COLOR[last.level];

  return (
    <svg
      width="100%"
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="xMinYMid meet"
      className="max-w-full"
      role="img"
      aria-label={`14-day risk trend, ${points.length} assessment${points.length === 1 ? "" : "s"}`}
    >
      {/* baseline rule */}
      <line x1={PAD_X} y1={height - PAD_Y} x2={width - PAD_X} y2={height - PAD_Y}
            stroke="rgba(56,67,81,0.08)" strokeDasharray="3 3" />

      {/* trend line */}
      {points.length > 1 && (
        <path d={path} fill="none" stroke="#384351" strokeWidth={1.25}
              strokeLinecap="round" strokeLinejoin="round" />
      )}

      {/* dots */}
      {points.map((p, i) => (
        <circle
          key={i}
          cx={p.x}
          cy={p.y}
          r={2.5}
          fill={RISK_COLOR[p.level]}
          stroke="#fff"
          strokeWidth={1}
        />
      ))}

      {/* current marker — slightly larger ring */}
      <circle cx={last.x} cy={last.y} r={4.5} fill="none"
              stroke={lastColor} strokeWidth={1.25} opacity={0.6} />
    </svg>
  );
}
