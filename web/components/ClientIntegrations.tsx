"use client";

/**
 * Client Detail — CareConnect / Revel / Directed Logic integration hub.
 * Identity belongs to the person (ai_agent), not a Watcher.
 */

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Copy, KeyRound, Loader2, Unlink2 } from "lucide-react";
import { apiDelete, apiGet, apiPost, apiPut, ApiError } from "@/lib/api";
import { classNames } from "@/lib/format";
import { Modal } from "@/components/Modal";
import type { ClientIntegration } from "@/lib/types";

interface Props {
  agentId: string;
}

type Panel = "careconnect" | "revel" | null;

export function ClientIntegrations({ agentId }: Props) {
  const [items, setItems] = useState<ClientIntegration[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [panel, setPanel] = useState<Panel>(null);
  const [onceSecret, setOnceSecret] = useState<string | null>(null);
  const [revelKey, setRevelKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);

  const refresh = useCallback(async () => {
    const data = await apiGet<{ list: ClientIntegration[] }>(
      `/agent/${agentId}/integrations`,
    );
    setItems(data.list);
  }, [agentId]);

  useEffect(() => {
    let cancelled = false;
    refresh()
      .then(() => { if (!cancelled) setLoadError(null); })
      .catch((e) => {
        if (!cancelled) setLoadError(e instanceof ApiError ? e.message : "Failed to load integrations.");
      });
    return () => { cancelled = true; };
  }, [refresh]);

  const cc = items?.find((i) => i.provider === "careconnect");
  const revel = items?.find((i) => i.provider === "revel");
  const directed = items?.find((i) => i.provider === "directed_logic");

  function openPanel(next: Panel) {
    setErr(null);
    setBusy(false);
    setCopied(false);
    setConfirmDisconnect(false);
    setRevelKey("");
    setOnceSecret(null);
    setPanel(next);
  }

  async function connectCareConnect() {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      const created = await apiPost<ClientIntegration>(
        `/agent/${agentId}/integrations/careconnect`,
      );
      setOnceSecret(created.secret ?? null);
      await refresh();
      setPanel("careconnect");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not connect CareConnect.");
      setPanel("careconnect");
    } finally {
      setBusy(false);
    }
  }

  async function onCareConnectClick() {
    if (cc?.connected) {
      openPanel("careconnect");
      return;
    }
    openPanel("careconnect");
    await connectCareConnect();
  }

  async function rotateSecret() {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      const rotated = await apiPost<ClientIntegration>(
        `/agent/${agentId}/integrations/careconnect/rotate`,
      );
      setOnceSecret(rotated.secret ?? null);
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not rotate secret.");
    } finally {
      setBusy(false);
    }
  }

  async function saveRevel() {
    if (busy || !revelKey.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await apiPut(`/agent/${agentId}/integrations/revel`, { apiKey: revelKey.trim() });
      setRevelKey("");
      setOnceSecret(null);
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not save Revel key.");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect(provider: "careconnect" | "revel") {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await apiDelete(`/agent/${agentId}/integrations/${provider}`);
      setConfirmDisconnect(false);
      setOnceSecret(null);
      setPanel(null);
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not disconnect.");
    } finally {
      setBusy(false);
    }
  }

  async function copyCredentials() {
    const publicId = cc?.publicId ?? "";
    const secret = onceSecret ?? "";
    const text = secret
      ? `Integration ID: ${publicId}\nSecret: ${secret}`
      : `Integration ID: ${publicId}`;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* ignore */ }
  }

  if (loadError) {
    return (
      <div className="text-[12px] text-risk-urgent">{loadError}</div>
    );
  }

  if (!items) {
    return <div className="skeleton h-10 w-full rounded-card" />;
  }

  return (
    <>
      <button
        type="button"
        className="btn-secondary w-full"
        onClick={onCareConnectClick}
        disabled={busy && panel === "careconnect" && !cc?.connected}
      >
        {busy && panel === "careconnect" && !cc?.connected ? (
          <Loader2 size={14} className="animate-spin" />
        ) : null}
        {cc?.connected ? "CareConnect connected" : "Connect to CareConnect"}
      </button>
      <button
        type="button"
        className="btn-secondary w-full"
        onClick={() => openPanel("revel")}
      >
        {revel?.connected ? "Revel connected" : "Connect to Revel"}
      </button>
      <button
        type="button"
        className="btn-secondary w-full opacity-60 cursor-not-allowed"
        disabled
        title="Coming soon"
        aria-disabled="true"
      >
        Connect to Directed Logic
      </button>
      <div className="text-[11px] text-slate-muted px-1">
        {directed?.comingSoon ? "Directed Logic — Coming soon" : "Coming soon"}
      </div>

      <Modal
        open={panel === "careconnect"}
        onClose={() => { if (!busy) setPanel(null); }}
        title={cc?.connected ? "CareConnect" : "Connect to CareConnect"}
        size="md"
        footer={
          confirmDisconnect ? (
            <>
              <button className="btn-secondary" disabled={busy} onClick={() => setConfirmDisconnect(false)}>
                Cancel
              </button>
              <button className="btn-danger" disabled={busy} onClick={() => disconnect("careconnect")}>
                {busy && <Loader2 size={14} className="animate-spin" />}
                Disconnect
              </button>
            </>
          ) : (
            <>
              <button className="btn-secondary" disabled={busy} onClick={() => setPanel(null)}>
                Close
              </button>
              {cc?.connected && (
                <button
                  className="btn-ghost text-risk-urgent"
                  disabled={busy}
                  onClick={() => setConfirmDisconnect(true)}
                >
                  <Unlink2 size={14} /> Disconnect
                </button>
              )}
            </>
          )
        }
      >
        {confirmDisconnect ? (
          <p className="text-[14px] text-slate-deep leading-relaxed">
            This will disconnect CareConnect from this client. The client/person, Watchers, chat history, assessments, and reminders will not be deleted.
          </p>
        ) : (
          <div className="space-y-4 text-[14px] text-slate-deep">
            {cc?.connected ? (
              <>
                <div>
                  <div className="label">Integration ID</div>
                  <code className="font-mono text-[13px]">{cc.publicId}</code>
                </div>
                <div>
                  <div className="label">Secret</div>
                  {onceSecret ? (
                    <code className="font-mono text-[13px] break-all">{onceSecret}</code>
                  ) : (
                    <span className="font-mono tracking-widest">••••••••••••••••••••</span>
                  )}
                  {onceSecret && (
                    <p className="helper mt-1">Copy this secret now. It will not be shown again.</p>
                  )}
                </div>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="btn-secondary text-[12px]" onClick={copyCredentials}>
                    <Copy size={12} /> {copied ? "Copied" : "Copy credentials"}
                  </button>
                  <button type="button" className="btn-secondary text-[12px]" disabled={busy} onClick={rotateSecret}>
                    {busy ? <Loader2 size={12} className="animate-spin" /> : <KeyRound size={12} />}
                    Rotate secret
                  </button>
                </div>
              </>
            ) : (
              <p className="text-slate-muted">Creating a CareConnect integration…</p>
            )}
            {err && (
              <div className="text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
                {err}
              </div>
            )}
          </div>
        )}
      </Modal>

      <Modal
        open={panel === "revel"}
        onClose={() => { if (!busy) setPanel(null); }}
        title={revel?.connected ? "Revel" : "Connect to Revel"}
        size="md"
        footer={
          confirmDisconnect ? (
            <>
              <button className="btn-secondary" disabled={busy} onClick={() => setConfirmDisconnect(false)}>
                Cancel
              </button>
              <button className="btn-danger" disabled={busy} onClick={() => disconnect("revel")}>
                {busy && <Loader2 size={14} className="animate-spin" />}
                Disconnect
              </button>
            </>
          ) : (
            <>
              <button className="btn-secondary" disabled={busy} onClick={() => setPanel(null)}>
                Close
              </button>
              {revel?.connected && (
                <button
                  className="btn-ghost text-risk-urgent"
                  disabled={busy}
                  onClick={() => setConfirmDisconnect(true)}
                >
                  <Unlink2 size={14} /> Disconnect
                </button>
              )}
            </>
          )
        }
      >
        {confirmDisconnect ? (
          <p className="text-[14px] text-slate-deep leading-relaxed">
            This will disconnect Revel from this client. The client/person, Watchers, chat history, assessments, and reminders will not be deleted.
          </p>
        ) : (
          <div className="space-y-4 text-[14px] text-slate-deep">
            {revel?.connected && (
              <div>
                <div className="kicker mb-1">Revel: Connected</div>
                <div className="label">API key</div>
                <span className="font-mono">{revel.maskedKey || `••••••••••••${revel.secretHint || ""}`}</span>
              </div>
            )}
            <div>
              <label htmlFor="revel-key" className="label">
                {revel?.connected ? "Replace key" : "Revel API key"}
              </label>
              <input
                id="revel-key"
                className="input font-mono"
                type="password"
                autoComplete="off"
                placeholder={revel?.connected ? "Paste a new Revel API key" : "Paste the Revel API key"}
                value={revelKey}
                onChange={(e) => setRevelKey(e.target.value)}
              />
              <div className="helper">The full key is stored encrypted and is never shown again.</div>
            </div>
            <button
              type="button"
              className="btn-primary text-[12px]"
              disabled={busy || revelKey.trim().length < 8}
              onClick={saveRevel}
            >
              {busy && <Loader2 size={12} className="animate-spin" />}
              {revel?.connected ? "Replace key" : "Save key"}
            </button>
            {err && (
              <div className="flex items-start gap-2 text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                {err}
              </div>
            )}
          </div>
        )}
      </Modal>
    </>
  );
}
