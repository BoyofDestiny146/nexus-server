"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { apiGet, ApiError } from "@/lib/api";
import { classNames, longTime } from "@/lib/format";
import {
  revelBadgeLabel,
  revelStatusTone,
  revelYesNo,
  type RevelStatus,
} from "@/lib/revelStatus";
import { Modal } from "@/components/Modal";

export function RevelStatusBadge({
  agentId,
  refreshKey = 0,
}: {
  agentId: string;
  refreshKey?: number;
}) {
  const [status, setStatus] = useState<RevelStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const data = await apiGet<RevelStatus>(`/agent/${agentId}/revel/status`);
      setStatus(data);
      setError(null);
    } catch (e) {
      setStatus(null);
      setError(e instanceof ApiError ? e.message : "Could not load Revel status.");
    } finally {
      setLoading(false);
    }
  }, [agentId]);

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
          <dl className="space-y-2 text-[13px] text-slate-deep">
            <Row label="Enabled" value={revelYesNo(status.enabled)} />
            <Row label="Auto Trigger" value={revelYesNo(status.autoTrigger)} />
            <Row label="Revel Tag" value={status.tag || "—"} mono />
            <Row label="Device Key" value={status.deviceKey || "—"} mono />
            <Row label="Revel Player" value={status.device?.name || "—"} />
            <Row label="Player Status" value={status.device?.status || "unknown"} />
            <div className="pt-2 mt-2 border-t border-slate-line/70">
              <div className="kicker mb-2">Last Revel Event</div>
              {status.lastEvent ? (
                <div className="space-y-2">
                  <Row label="Intent" value={status.lastEvent.intent || "—"} />
                  <Row label="Screen" value={status.lastEvent.screen || "—"} />
                  <Row label="Message" value={status.lastEvent.summary || "—"} />
                  <Row label="Sent At" value={longTime(status.lastEvent.createdAt) || "—"} />
                  <Row label="Result" value={(status.lastEvent.result || "—").toString().toUpperCase()} />
                  <Row label="Reason" value={status.lastEvent.reason || "—"} />
                  <Row label="Error" value={status.lastEvent.error || "—"} />
                </div>
              ) : (
                <p className="text-slate-muted">No Revel events recorded for this discussion yet.</p>
              )}
            </div>
          </dl>
        ) : (
          <p className="text-[13px] text-slate-muted">
            {error || "Revel status is not available for this discussion."}
          </p>
        )}
      </Modal>
    </>
  );
}

function Row({
  label, value, mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-slate-muted shrink-0">{label}</dt>
      <dd className={classNames("text-right min-w-0 break-all", mono && "font-mono text-[12px]")}>
        {value}
      </dd>
    </div>
  );
}
