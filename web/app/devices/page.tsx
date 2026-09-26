"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Search, Cpu, Wifi, Volume2, Pencil, Check, X, Loader2 } from "lucide-react";
import { apiGet, apiPatch, ApiError } from "@/lib/api";
import type { DeviceRow } from "@/lib/types";
import { classNames, relativeTime } from "@/lib/format";
import { AppShell, PageHeader } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { EmptyState } from "@/components/EmptyState";
import { VoiceSelector } from "@/components/VoiceSelector";

interface PagedDevices {
  list: DeviceRow[];
  total: number;
  page: number;
  limit: number;
}

const LIMIT = 25;

function isOnline(lastConnectedAt: string | null | undefined): boolean {
  if (!lastConnectedAt) return false;
  const t = new Date(lastConnectedAt).getTime();
  return Date.now() - t < 5 * 60_000;
}

// ── Inline Device ID editor (client's external id, PATCH /device/{id}/client-id)

function ClientIdCell({ d }: { d: DeviceRow }) {
  const [value, setValue] = useState(d.clientDeviceId ?? "");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function begin() {
    setDraft(value);
    setErr(null);
    setEditing(true);
  }

  async function save() {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      await apiPatch(`/device/${d.id}/client-id`, {
        clientDeviceId: draft.trim() || null,
      });
      setValue(draft.trim());
      setEditing(false);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Update failed.");
    } finally {
      setBusy(false);
    }
  }

  if (!editing) {
    return (
      <div className="flex items-center gap-1.5 min-w-0">
        <span className={classNames("font-mono text-[12px] truncate", value ? "text-slate-deep" : "text-slate-muted")}>
          {value || "—"}
        </span>
        <button
          type="button"
          onClick={begin}
          aria-label="Edit device ID"
          className="p-1 rounded text-slate-muted/70 hover:text-slate-deep hover:bg-slate-line/40 shrink-0"
        >
          <Pencil size={12} />
        </button>
      </div>
    );
  }

  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1">
        <input
          className="input font-mono !h-7 !text-[12px] !px-2 min-w-0 w-36"
          maxLength={64}
          autoFocus
          placeholder="external id"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") save();
            if (e.key === "Escape") setEditing(false);
          }}
          disabled={busy}
        />
        <button
          type="button"
          onClick={save}
          disabled={busy}
          aria-label="Save device ID"
          className="p-1 rounded text-teal hover:bg-teal-tint shrink-0"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
        </button>
        <button
          type="button"
          onClick={() => setEditing(false)}
          disabled={busy}
          aria-label="Cancel"
          className="p-1 rounded text-slate-muted hover:bg-slate-line/40 shrink-0"
        >
          <X size={13} />
        </button>
      </div>
      {err && <div className="mt-1 text-[11px] text-risk-urgent">{err}</div>}
    </div>
  );
}

// ── Desktop table row ─────────────────────────────────────────────────────────

function DeviceTableRow({ d, onDeleted }: { d: DeviceRow; onDeleted: () => void }) {
  const [voiceOpen, setVoiceOpen] = useState(false);
  const online = isOnline(d.lastConnectedAt);

  return (
    <tr className="border-b border-slate-line/50 last:border-b-0 hover:bg-bone-soft/60 transition">
      <td className="px-5 py-4 font-mono text-[13px] text-slate-deep tracking-tight">
        {d.macAddress}
      </td>
      <td className="px-5 py-4">
        {d.agentId ? (
          <Link href={`/patients/${d.agentId}`} className="text-teal-deep hover:underline">
            {d.agentName ?? "—"}
          </Link>
        ) : (
          <span className="text-slate-muted">unbound</span>
        )}
      </td>
      <td className="px-5 py-4 text-slate">{d.alias ?? "—"}</td>
      <td className="px-5 py-4"><ClientIdCell d={d} /></td>
      <td className="px-5 py-4 text-slate-muted text-[12px]">{d.board ?? "—"}</td>
      <td className="px-5 py-4">
        <div className="flex items-center gap-2">
          {online && (
            <span className="inline-flex items-center gap-1.5 chip-teal text-[11px]">
              <Wifi size={10} /> online
            </span>
          )}
          <span className="text-[12px] text-slate-muted num">
            {d.lastConnectedAt ? relativeTime(d.lastConnectedAt) : "never"}
          </span>
        </div>
      </td>
      <td className="px-5 py-4">
        <button
          type="button"
          onClick={() => setVoiceOpen(true)}
          className={classNames(
            "inline-flex items-center gap-1.5 text-[12px] tracking-tight transition px-2.5 py-1 rounded-card border",
            voiceOpen
              ? "bg-teal-tint border-teal/20 text-teal-deep"
              : "border-slate-line/70 text-slate-muted hover:text-slate-deep hover:border-slate-line",
          )}
          aria-haspopup="dialog"
          aria-expanded={voiceOpen}
          aria-label="Control"
        >
          <Volume2 size={12} strokeWidth={1.75} />
          Control
        </button>
        {voiceOpen && (
          <VoiceSelector
            open={voiceOpen}
            onClose={() => setVoiceOpen(false)}
            mac={d.macAddress}
            deviceId={d.id}
            agentId={d.agentId}
            agentName={d.agentName}
            onDeleted={() => {
              setVoiceOpen(false);
              onDeleted();
            }}
          />
        )}
      </td>
    </tr>
  );
}

// ── Mobile card item ──────────────────────────────────────────────────────────

function DeviceCard({ d, onDeleted }: { d: DeviceRow; onDeleted: () => void }) {
  const [voiceOpen, setVoiceOpen] = useState(false);
  const online = isOnline(d.lastConnectedAt);

  return (
    <li className="px-5 py-4 border-b border-slate-line/50 last:border-b-0">
      <div className="flex items-center justify-between gap-3 mb-1">
        <span className="font-mono text-[13px] text-slate-deep tracking-tight">{d.macAddress}</span>
        {online && (
          <span className="chip-teal text-[10px]">
            <Wifi size={9} /> online
          </span>
        )}
      </div>
      <div className="text-[13px] text-slate">
        {d.agentName ?? <span className="text-slate-muted">unbound</span>}
      </div>
      {d.alias && <div className="text-[12px] text-slate-muted">{d.alias}</div>}
      <div className="mt-1.5"><ClientIdCell d={d} /></div>
      <div className="mt-1.5 text-[11px] uppercase tracking-[0.12em] text-slate-muted num">
        {d.lastConnectedAt ? relativeTime(d.lastConnectedAt) : "never"}
      </div>

      <div className="mt-3">
        <button
          type="button"
          onClick={() => setVoiceOpen(true)}
          className={classNames(
            "inline-flex items-center gap-1.5 text-[12px] tracking-tight transition px-2.5 py-1 rounded-card border",
            voiceOpen
              ? "bg-teal-tint border-teal/20 text-teal-deep"
              : "border-slate-line/70 text-slate-muted hover:text-slate-deep hover:border-slate-line",
          )}
          aria-haspopup="dialog"
          aria-expanded={voiceOpen}
          aria-label="Control"
        >
          <Volume2 size={12} strokeWidth={1.75} />
          Control
        </button>
        {voiceOpen && (
          <VoiceSelector
            open={voiceOpen}
            onClose={() => setVoiceOpen(false)}
            mac={d.macAddress}
            deviceId={d.id}
            agentId={d.agentId}
            agentName={d.agentName}
            onDeleted={() => {
              setVoiceOpen(false);
              onDeleted();
            }}
          />
        )}
      </div>
    </li>
  );
}

// ── Main page view ────────────────────────────────────────────────────────────

function DevicesView() {
  const [data, setData] = useState<PagedDevices | null>(null);
  const [page, setPage] = useState(1);
  const [keywords, setKeywords] = useState("");
  const [pendingKw, setPendingKw] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);

  // debounce keyword to avoid hammering the API
  useEffect(() => {
    const t = setTimeout(() => setKeywords(pendingKw), 250);
    return () => clearTimeout(t);
  }, [pendingKw]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    apiGet<PagedDevices>(
      `/admin/device/all?page=${page}&limit=${LIMIT}` +
        (keywords ? `&keywords=${encodeURIComponent(keywords)}` : ""),
    )
      .then((d) => { if (!cancelled) { setData(d); setError(null); } })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : "Failed."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [page, keywords, refreshNonce]);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / LIMIT)) : 1;

  return (
    <>
      <PageHeader
        kicker="Hardware"
        title="Devices"
        subtitle="Every Watcher registered on this server. The Last connected column shows online (≤5 min) at a glance. Use the Control button on each row to configure voice, speed, and other device settings."
      />

      <section className="px-8 md:px-12 py-8">
        <div className="flex items-center justify-between gap-4 mb-6">
          <div className="relative flex-1 max-w-sm">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-muted" />
            <input
              type="search"
              value={pendingKw}
              onChange={(e) => { setPendingKw(e.target.value); setPage(1); }}
              placeholder="Filter by MAC, alias, or client"
              className="input pl-9"
              aria-label="Filter devices"
            />
          </div>
          {data && (
            <div className="text-[12px] uppercase tracking-[0.12em] text-slate-muted">
              {data.total} {data.total === 1 ? "device" : "devices"}
            </div>
          )}
        </div>

        {error && (
          <div className="card p-6 border-risk-urgent/30 bg-risk-urgent/5 text-risk-urgent text-[14px] mb-4">
            {error}
          </div>
        )}

        {loading && !data && <div className="card p-8 skeleton h-72" />}

        {data && data.list.length === 0 && !loading && (
          <EmptyState
            icon={Cpu}
            title="No devices yet"
            body="Pair a Watcher to a client on their detail page, or use the auto-pick step in the onboarding wizard."
          />
        )}

        {data && data.list.length > 0 && (
          <div className="card overflow-hidden">
            {/* Table — collapses to cards on small screens */}
            <table className="hidden md:table w-full text-[14px]">
              <thead>
                <tr className="text-[11px] uppercase tracking-[0.12em] text-slate-muted bg-bone-soft border-b border-slate-line/70">
                  <th className="text-left font-medium px-5 py-3">MAC / EUI</th>
                  <th className="text-left font-medium px-5 py-3">Client</th>
                  <th className="text-left font-medium px-5 py-3">Alias</th>
                  <th className="text-left font-medium px-5 py-3">Device ID</th>
                  <th className="text-left font-medium px-5 py-3">Board</th>
                  <th className="text-left font-medium px-5 py-3">Last connected</th>
                  <th className="text-left font-medium px-5 py-3">Control</th>
                </tr>
              </thead>
              <tbody>
                {data.list.map((d) => (
                  <DeviceTableRow
                    key={d.id}
                    d={d}
                    onDeleted={() => setRefreshNonce((n) => n + 1)}
                  />
                ))}
              </tbody>
            </table>

            {/* Card list for mobile */}
            <ul className="md:hidden divide-y divide-slate-line/50">
              {data.list.map((d) => (
                <DeviceCard
                  key={d.id}
                  d={d}
                  onDeleted={() => setRefreshNonce((n) => n + 1)}
                />
              ))}
            </ul>
          </div>
        )}

        {data && totalPages > 1 && (
          <div className="mt-6 flex items-center justify-between text-[13px] text-slate-muted">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="btn-ghost text-[12px]"
            >
              ← Previous
            </button>
            <span>page {page} of {totalPages}</span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="btn-ghost text-[12px]"
            >
              Next →
            </button>
          </div>
        )}
      </section>
    </>
  );
}

export default function DevicesPage() {
  return (
    <RequireAuth>
      <AppShell>
        <DevicesView />
      </AppShell>
    </RequireAuth>
  );
}
