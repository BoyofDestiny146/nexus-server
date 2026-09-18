"use client";

import Link from "next/link";
import { ExternalLink, BookOpen, Activity } from "lucide-react";
import { AppShell, PageHeader } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";

function SettingsView() {
  return (
    <>
      <PageHeader
        kicker="System"
        title="Settings"
        subtitle="careconnect runs entirely on the local server — local LLM, local TTS, local DB. Nothing leaves the network."
      />

      <section className="px-8 md:px-12 py-8 grid grid-cols-1 md:grid-cols-2 gap-6 max-w-5xl">
        <a
          href="/api/docs"
          target="_blank" rel="noopener"
          className="card card-hover p-7 group"
        >
          <div className="flex items-start justify-between">
            <div className="w-11 h-11 rounded-2xl bg-teal-tint text-teal-deep grid place-items-center">
              <BookOpen size={20} strokeWidth={1.6} />
            </div>
            <ExternalLink size={14} className="text-slate-muted group-hover:text-slate-deep transition" />
          </div>
          <h2 className="display-3 mt-5 text-slate-deep">API reference</h2>
          <p className="mt-2 text-[13.5px] text-slate-muted leading-relaxed">
            FastAPI auto-generated docs. Lists every REST endpoint the dashboard consumes — useful when wiring scripts or debugging.
          </p>
          <div className="mt-5 text-[13px] text-teal-deep tracking-tight">/api/docs →</div>
        </a>

        <div className="card p-7 md:col-span-2">
          <div className="flex items-center gap-3 mb-5">
            <div className="w-9 h-9 rounded-2xl bg-bone-soft text-slate-deep grid place-items-center">
              <Activity size={18} strokeWidth={1.6} />
            </div>
            <h2 className="display-3 text-slate-deep">About this install</h2>
          </div>
          <p className="text-[14px] text-slate-deep leading-relaxed max-w-3xl">
            careconnect is the staff console for at-risk elderly client companions. Each W1-A Watcher connects to the local server via the XiaoZhi voice protocol; conversations are answered by a local LLM with a soothing caregiver persona and spoken back through Piper TTS. Everything in the client roster, every chat row, every risk assessment — all of it lives on this device.
          </p>
          <dl className="mt-7 grid grid-cols-2 sm:grid-cols-3 gap-y-5 text-[13px]">
            <Field label="Build" value="careconnect 0.2.0" />
            <Field label="API" value="/api · FastAPI on :8080" />
            <Field label="Network" value="LAN · TLS internal" />
          </dl>
        </div>
      </section>
    </>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-[0.14em] text-slate-muted mb-1">{label}</dt>
      <dd className="text-slate-deep font-mono text-[12px]">{value}</dd>
    </div>
  );
}

export default function SettingsPage() {
  return (
    <RequireAuth>
      <AppShell>
        <SettingsView />
      </AppShell>
    </RequireAuth>
  );
}
