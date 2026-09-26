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
  centered = false,
  panelClassName,
  zClassName = "z-[80]",
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  size?: "sm" | "md" | "lg" | "xl" | "workspace";
  /** Vertically center the panel instead of pinning it below the top padding. */
  centered?: boolean;
  panelClassName?: string;
  zClassName?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    document.addEventListener("keydown", onKey);
    // Focus the dialog only when it opens. Re-running this on every parent
    // render (inline onClose identity) steals focus from inputs after each key.
    const node = ref.current;
    if (node && !node.contains(document.activeElement)) {
      node.focus();
    }
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open]);

  if (!open) return null;
  if (typeof document === "undefined") return null;

  const widthCls =
    size === "sm" ? "max-w-md"
    : size === "lg" ? "max-w-3xl"
    : size === "xl" ? "max-w-4xl"
    : size === "workspace" ? "max-w-5xl"
    : "max-w-xl";
  const overlayPad = centered
    ? "items-center py-6 sm:py-10"
    : size === "workspace" ? "items-start pt-6 sm:pt-10 pb-6"
    : "items-start pt-24";
  const scrollable = centered || size === "workspace";
  const panelExtra = scrollable ? "max-h-[min(92vh,58rem)] flex flex-col" : "";
  const bodyExtra = scrollable ? "overflow-y-auto min-h-0 flex-1" : "";

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={title ? "modal-title" : undefined}
      className={classNames(
        "fixed inset-0 flex justify-center px-4",
        zClassName,
        overlayPad,
      )}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCloseRef.current(); }}
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
          panelExtra,
          panelClassName,
        )}
        onMouseDown={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
      >
        {title && (
          <div className="flex items-start justify-between px-6 pt-5 pb-4 border-b border-slate-line/70 shrink-0">
            {title && (
              <h2 id="modal-title" className="display-3 text-slate-deep">
                {title}
              </h2>
            )}
            <button
              type="button"
              onClick={() => onCloseRef.current()}
              className="text-slate-muted hover:text-slate-deep p-1 -mr-1 -mt-1 rounded transition"
              aria-label="Close dialog"
            >
              <X size={18} />
            </button>
          </div>
        )}
        <div className={classNames("px-6 py-5", bodyExtra)}>{children}</div>
        {footer && (
          <div className="px-6 py-4 border-t border-slate-line/70 bg-bone-soft rounded-b-card flex items-center justify-end gap-3 shrink-0">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
