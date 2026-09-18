"use client";

import { Check } from "lucide-react";
import { classNames } from "@/lib/format";

export interface WizardStep {
  key: string;
  label: string;
  description?: string;
}

export function WizardStepper({
  steps,
  current,
  onJump,
}: {
  steps: WizardStep[];
  current: number;
  onJump?: (i: number) => void;
}) {
  return (
    <ol className="flex items-stretch w-full">
      {steps.map((s, i) => {
        const done = i < current;
        const active = i === current;
        const clickable = !!onJump && i <= current;
        return (
          <li key={s.key} className="flex-1 flex items-stretch">
            <button
              type="button"
              disabled={!clickable}
              onClick={() => clickable && onJump?.(i)}
              className={classNames(
                "group flex-1 flex items-start gap-3 text-left py-4 px-3 transition",
                clickable ? "cursor-pointer hover:bg-bone-soft" : "cursor-default",
              )}
            >
              <span
                className={classNames(
                  "shrink-0 w-7 h-7 rounded-full grid place-items-center text-[12px] font-medium transition",
                  done    && "bg-teal text-white",
                  active  && "bg-slate-deep text-white",
                  !done && !active && "bg-bone-soft border border-slate-line text-slate-muted",
                )}
              >
                {done ? <Check size={14} strokeWidth={2.5} /> : i + 1}
              </span>
              <div className="flex flex-col">
                <span className={classNames(
                  "text-[11px] uppercase tracking-[0.12em]",
                  active ? "text-slate-deep" : "text-slate-muted",
                )}>
                  Step {i + 1}
                </span>
                <span className={classNames(
                  "font-display text-[18px] tracking-display leading-tight",
                  active ? "text-slate-deep" : done ? "text-slate" : "text-slate-muted",
                )}>
                  {s.label}
                </span>
              </div>
            </button>
            {i < steps.length - 1 && (
              <div className="self-center w-6 h-px bg-slate-line/80" aria-hidden />
            )}
          </li>
        );
      })}
    </ol>
  );
}
