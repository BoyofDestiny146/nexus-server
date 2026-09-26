"use client";

import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { classNames } from "@/lib/format";

export function Drawer({
  open,
  onClose,
  title,
  children,
  footer,
  side = "left",
  testId,
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  side?: "left" | "right";
  testId?: string;
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

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={title ? "drawer-title" : undefined}
      data-testid={testId}
      className="fixed inset-0 z-[80] flex"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onCloseRef.current();
      }}
    >
      <div
        className="absolute inset-0 bg-slate-deep/30 backdrop-blur-[2px]"
        aria-hidden
      />
      <div
        ref={ref}
        tabIndex={-1}
        className={classNames(
          "relative z-10 pointer-events-auto flex flex-col w-full max-w-md h-full bg-white border-slate-line outline-none toast-in",
          side === "left" ? "mr-auto border-r" : "ml-auto border-l",
        )}
        onMouseDown={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
      >
        {title ? (
          <div className="flex items-start justify-between px-5 pt-5 pb-4 border-b border-slate-line/70 shrink-0">
            <h2 id="drawer-title" className="display-3 text-slate-deep">
              {title}
            </h2>
            <button
              type="button"
              onClick={() => onCloseRef.current()}
              className="text-slate-muted hover:text-slate-deep p-1 -mr-1 -mt-1 rounded transition"
              aria-label="Close drawer"
            >
              <X size={18} />
            </button>
          </div>
        ) : null}
        <div className="px-5 py-5 overflow-y-auto min-h-0 flex-1">{children}</div>
        {footer ? (
          <div className="px-5 py-4 border-t border-slate-line/70 bg-bone-soft flex items-center justify-end gap-3 shrink-0">
            {footer}
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
