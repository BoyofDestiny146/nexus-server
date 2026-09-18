"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { CheckCircle2, AlertTriangle, Info, X } from "lucide-react";
import { classNames } from "@/lib/format";

type ToastVariant = "success" | "error" | "info";

interface ToastItem {
  id: number;
  message: string;
  variant: ToastVariant;
}

interface ToastContextShape {
  push: (msg: string, variant?: ToastVariant) => void;
}

const Ctx = createContext<ToastContextShape | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const idRef = useRef(0);

  const push = useCallback((message: string, variant: ToastVariant = "info") => {
    const id = ++idRef.current;
    setItems((prev) => [...prev, { id, message, variant }]);
    setTimeout(() => {
      setItems((prev) => prev.filter((t) => t.id !== id));
    }, 4500);
  }, []);

  const dismiss = (id: number) => setItems((prev) => prev.filter((t) => t.id !== id));

  return (
    <Ctx.Provider value={{ push }}>
      {children}
      <div className="fixed top-5 right-5 z-50 flex flex-col gap-2 max-w-sm">
        {items.map((t) => (
          <div
            key={t.id}
            role="status"
            className={classNames(
              "toast-in flex items-start gap-3 px-3.5 py-3 rounded-card border bg-white shadow-sm",
              t.variant === "success" && "border-teal/30",
              t.variant === "error"   && "border-risk-urgent/30",
              t.variant === "info"    && "border-slate-line/80",
            )}
          >
            <span className="mt-0.5">
              {t.variant === "success" && <CheckCircle2 size={16} className="text-teal" />}
              {t.variant === "error"   && <AlertTriangle size={16} className="text-risk-urgent" />}
              {t.variant === "info"    && <Info size={16} className="text-slate-muted" />}
            </span>
            <p className="text-[13px] leading-snug text-slate-deep flex-1">{t.message}</p>
            <button
              onClick={() => dismiss(t.id)}
              className="text-slate-muted hover:text-slate-deep transition"
              aria-label="Dismiss notification"
            >
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useToast() must be used inside <ToastProvider>");
  return ctx;
}

/** Test for ToastProvider presence without throwing — useful for SSR safety. */
export function useOptionalToast() {
  return useContext(Ctx);
}
