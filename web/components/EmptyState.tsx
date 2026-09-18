"use client";

import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

export function EmptyState({
  icon: Icon,
  title,
  body,
  action,
  className = "",
}: {
  icon?: LucideIcon;
  title: string;
  body?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`flex flex-col items-center justify-center text-center py-20 px-6 ${className}`}>
      {Icon && (
        <div className="w-14 h-14 rounded-full border border-slate-line/80 bg-bone-soft grid place-items-center text-slate-muted mb-6">
          <Icon size={22} strokeWidth={1.5} />
        </div>
      )}
      <h3 className="display-3 text-slate-deep">{title}</h3>
      {body && (
        <p className="mt-2 max-w-md text-[14px] text-slate-muted leading-relaxed">{body}</p>
      )}
      {action && <div className="mt-6">{action}</div>}
    </div>
  );
}
