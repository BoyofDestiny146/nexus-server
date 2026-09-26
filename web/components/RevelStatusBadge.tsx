"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { apiGet, ApiError } from "@/lib/api";
import { classNames } from "@/lib/format";
import {
  revelBadgeLabel,
  revelStatusTone,
  type RevelStatus,
} from "@/lib/revelStatus";
import { Modal } from "@/components/Modal";
import { RevelDiagnosticsList } from "@/components/RevelDiagnosticsList";

export function RevelStatusBadge({
  agentId,
  refreshKey = 0,
  onStatus,
}: {
  agentId: string;
  refreshKey?: number;
  onStatus?: (status: RevelStatus | null) => void;
}) {
  const [status, setStatus] = useState<RevelStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const data = await apiGet<RevelStatus>(`/agent/${agentId}/revel/status`);
      setStatus(data);
      onStatus?.(data);
      setError(null);
    } catch (e) {
      setStatus(null);
      onStatus?.(null);
      setError(e instanceof ApiError ? e.message : "Could not load Revel status.");
    } finally {
      setLoading(false);
    }
  }, [agentId, onStatus]);

  useEffect(() => {
    setLoading(true);
    void load();
  }, [load, refreshKey]);

  useEffect(() => {
    if (!open) return;
    void load();
  }, [open, load]);

  const tone = revelStatusTone(status?.mode);
  const label = loading
    ? "Revel…"
    : error
      ? "Revel status unavailable"
      : revelBadgeLabel(status);
  const dot =
    tone === "enabled" ? "bg-teal" : tone === "manual" ? "bg-risk-moderate" : "bg-slate-line";
  const text =
    tone === "enabled"
      ? "text-teal-deep"
      : tone === "manual"
        ? "text-risk-moderate"
        : "text-slate-muted";

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={label}
        aria-label={`Revel diagnostics. ${label}`}
        className={classNames(
          "inline-flex items-center gap-1.5 max-w-[min(100%,22rem)] sm:max-w-[28rem] rounded-chip border border-slate-line/80 bg-white/80 px-2 py-1 text-left transition hover:border-slate-muted/40",
          text,
        )}
      >
        {loading ? (
          <Loader2 size={10} className="animate-spin shrink-0 text-slate-muted" />
        ) : (
          <span className={classNames("w-1.5 h-1.5 rounded-full shrink-0", dot)} aria-hidden />
        )}
        <span className="truncate text-[10px] uppercase tracking-[0.12em]">{label}</span>
      </button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Revel Integration"
        size="sm"
        centered
      >
        {status ? (
          <RevelDiagnosticsList status={status} />
        ) : (
          <p className="text-[13px] text-slate-muted">
            {error || "Revel status is not available for this discussion."}
          </p>
        )}
      </Modal>
    </>
  );
}
