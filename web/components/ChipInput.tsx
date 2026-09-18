"use client";

import { useRef, useState } from "react";
import { X, Plus } from "lucide-react";
import { classNames } from "@/lib/format";

export function ChipInput({
  value,
  onChange,
  placeholder,
  suggestions,
  ariaLabel,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  suggestions?: string[];
  ariaLabel?: string;
}) {
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  function commit(raw: string) {
    const v = raw.trim();
    if (!v) return;
    if (value.includes(v)) {
      setDraft("");
      return;
    }
    onChange([...value, v]);
    setDraft("");
  }

  function remove(v: string) {
    onChange(value.filter((x) => x !== v));
  }

  const remainingSuggestions = (suggestions ?? []).filter((s) => !value.includes(s));

  return (
    <div>
      <div
        className={classNames(
          "min-h-[42px] w-full bg-white border border-slate-line/80 rounded-card px-2 py-1.5",
          "focus-within:border-teal focus-within:ring-2 focus-within:ring-teal/15",
          "flex flex-wrap items-center gap-1.5 transition",
        )}
        onClick={() => inputRef.current?.focus()}
      >
        {value.map((v) => (
          <span key={v} className="chip">
            {v}
            <button
              type="button"
              onClick={() => remove(v)}
              className="text-slate-muted hover:text-slate-deep transition"
              aria-label={`Remove ${v}`}
            >
              <X size={11} />
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              commit(draft);
            } else if (e.key === "Backspace" && !draft && value.length > 0) {
              onChange(value.slice(0, -1));
            }
          }}
          onBlur={() => draft && commit(draft)}
          placeholder={value.length === 0 ? placeholder : ""}
          aria-label={ariaLabel}
          className="flex-1 min-w-[120px] bg-transparent outline-none text-[14px] text-slate-deep placeholder-slate-muted px-1 py-0.5"
        />
      </div>

      {remainingSuggestions.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {remainingSuggestions.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => commit(s)}
              className="inline-flex items-center gap-1 text-[11px] tracking-tight px-2 py-1 rounded-chip border border-slate-line/60 text-slate-muted hover:text-teal hover:border-teal/40 transition"
            >
              <Plus size={10} /> {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
