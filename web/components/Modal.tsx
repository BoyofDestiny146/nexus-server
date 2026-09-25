"use client";

import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { classNames } from "@/lib/format";

export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  size = "md",
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  size?: "sm" | "md" | "lg";
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    ref.current?.focus();
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  if (!open) return null;
  if (typeof document === "undefined") return null;

  const widthCls = size === "sm" ? "max-w-md" : size === "lg" ? "max-w-3xl" : "max-w-xl";

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={title ? "modal-title" : undefined}
      className="fixed inset-0 z-[80] flex items-start justify-center pt-24 px-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      {/* Must not intercept clicks. The overlay parent handles outside-click close. */}
      <div
        className="absolute inset-0 bg-slate-deep/30 backdrop-blur-[2px] pointer-events-none"
        aria-hidden
      />
      <div
        ref={ref}
        tabIndex={-1}
        className={classNames(
          "relative z-10 pointer-events-auto w-full bg-white border border-slate-line rounded-card outline-none toast-in",
          widthCls,
        )}
        onMouseDown={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
      >
        {title && (
          <div className="flex items-start justify-between px-6 pt-5 pb-4 border-b border-slate-line/70">
            {title && (
              <h2 id="modal-title" className="display-3 text-slate-deep">
                {title}
              </h2>
            )}
            <button
              type="button"
              onClick={onClose}
              className="text-slate-muted hover:text-slate-deep p-1 -mr-1 -mt-1 rounded transition"
              aria-label="Close dialog"
            >
              <X size={18} />
            </button>
          </div>
        )}
        <div className="px-6 py-5">{children}</div>
        {footer && (
          <div className="px-6 py-4 border-t border-slate-line/70 bg-bone-soft rounded-b-card flex items-center justify-end gap-3">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
