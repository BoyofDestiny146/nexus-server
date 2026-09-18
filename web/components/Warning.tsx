import type { ReactNode } from "react";

interface WarningProps {
  title?: string;
  children: ReactNode;
}

/**
 * Inline amber warning panel, distinct from the existing red error pattern
 * (text-risk-urgent). Use for "heads-up, requires confirmation" copy where
 * the action isn't an error per se but the operator must read + acknowledge.
 */
export function Warning({ title, children }: WarningProps) {
  return (
    <div className="text-[13px] border border-amber-400/40 bg-amber-50/60 text-amber-900 rounded-card px-3 py-2.5">
      {title ? <div className="font-medium mb-1">{title}</div> : null}
      <div>{children}</div>
    </div>
  );
}
