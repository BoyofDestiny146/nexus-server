"use client";

import Link from "next/link";
import type { KnowledgeBase } from "@/lib/types";
import { classNames } from "@/lib/format";

export function KnowledgeAccessFields({
  catalog,
  selectedIds,
  onToggle,
  loading,
  error,
}: {
  catalog: KnowledgeBase[];
  selectedIds: number[];
  onToggle: (id: number, checked: boolean) => void;
  loading?: boolean;
  error?: string | null;
}) {
  const selected = new Set(selectedIds);
  const visible = catalog.filter((kb) => kb.enabled || selected.has(kb.id));

  if (loading) {
    return (
      <div className="space-y-2">
        <div className="skeleton h-10 w-full rounded-card" />
        <div className="skeleton h-10 w-full rounded-card" />
        <div className="skeleton h-10 w-3/4 rounded-card" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
        {error}
      </div>
    );
  }

  if (visible.length === 0) {
    return (
      <p className="text-[14px] text-slate-muted leading-relaxed">
        No knowledge bases are enabled yet. Create them in Knowledge, then assign them here.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-[14px] text-slate-muted leading-relaxed">
        Bound Watchers inherit every Knowledge Base checked for this client. Revel tags on topics are stored for later; they do not run display commands yet.
      </p>
      <ul className="space-y-2">
        {visible.map((kb) => {
          const checked = selected.has(kb.id);
          const id = `kb-access-${kb.id}`;
          return (
            <li key={kb.id}>
              <label
                htmlFor={id}
                className={classNames(
                  "flex items-start gap-3 rounded-card border px-3.5 py-3 cursor-pointer transition",
                  checked
                    ? "bg-white border-teal/30"
                    : "bg-white border-slate-line/70 hover:border-slate-line",
                )}
              >
                <input
                  id={id}
                  type="checkbox"
                  className="mt-1 accent-teal"
                  checked={checked}
                  onChange={(e) => onToggle(kb.id, e.target.checked)}
                />
                <span className="min-w-0 flex-1">
                  <span className="flex items-start justify-between gap-2">
                    <span className="block text-[14px] text-slate-deep tracking-tight">{kb.name}</span>
                    <Link
                      href={`/knowledge/${kb.id}`}
                      onClick={(e) => e.stopPropagation()}
                      className="shrink-0 text-[11px] uppercase tracking-[0.12em] text-teal-deep hover:underline"
                    >
                      Open
                    </Link>
                  </span>
                  {kb.description ? (
                    <span className="block mt-0.5 text-[12px] text-slate-muted leading-snug">{kb.description}</span>
                  ) : null}
                  {!kb.enabled ? (
                    <span className="block mt-1 text-[11px] uppercase tracking-[0.12em] text-slate-muted">Disabled in catalog</span>
                  ) : null}
                </span>
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
