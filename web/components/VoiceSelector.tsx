"use client";

/**
 * VoiceSelector — per-device voice + speed picker.
 *
 * Renders Voice / Speed / Length, Preview, Unbind, Delete / Reset Device,
 * Save, plus volume, an editable test message, and Test Voice. Catalog from
 * GET /api/voices; current values from GET /api/device/{mac}/voice.
 */

import { useEffect, useRef, useState } from "react";
import { Volume2, Loader2, Check, AlertTriangle, Wifi, Trash2, Play, Unlink2 } from "lucide-react";
import { apiGet, apiPut, apiPost, apiBinary, apiDelete, ApiError } from "@/lib/api";
import { classNames } from "@/lib/format";
import { Modal } from "@/components/Modal";
import type { VoiceCatalog, DeviceVoice } from "@/lib/types";
import {
  coercePower,
  DEFAULT_POWER,
  powerHelp,
  SLEEP_MODES,
  SLEEP_TIMEOUTS,
  type DevicePowerSettings,
} from "@/lib/devicePower";

interface Props {
  mac: string;
  deviceId: string;
  agentId?: string | null;
  agentName?: string | null;
  onDeleted?: () => void;
}

type SaveStatus = "idle" | "saving" | "saved" | "error";
type PreviewStatus = "idle" | "loading" | "playing" | "error";

const DEFAULT_TEST_MESSAGE = "Hello, this is a test message from CareConnect.";

export function VoiceSelector({ mac, deviceId, agentId, onDeleted }: Props) {
  const [catalog, setCatalog] = useState<VoiceCatalog | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [selectedVoice, setSelectedVoice] = useState<string>("");
  const [selectedSpeed, setSelectedSpeed] = useState<string>("");
  const [selectedLength, setSelectedLength] = useState<string>("");
  const [selectedVolume, setSelectedVolume] = useState<number>(80);
  const [testMessage, setTestMessage] = useState<string>(DEFAULT_TEST_MESSAGE);
  const [power, setPower] = useState<DevicePowerSettings>(DEFAULT_POWER);
  const [powerApplyState, setPowerApplyState] = useState<DeviceVoice["powerApplyState"]>("unset");
  const [powerSaveMessage, setPowerSaveMessage] = useState<string | null>(null);

  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);

  const [previewStatus, setPreviewStatus] = useState<PreviewStatus>("idle");
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const [unbindOpen, setUnbindOpen] = useState(false);
  const [unbindBusy, setUnbindBusy] = useState(false);
  const [unbindError, setUnbindError] = useState<string | null>(null);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const blobUrlRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [cat, dev] = await Promise.all([
          apiGet<VoiceCatalog>("/voices"),
          apiGet<DeviceVoice>(`/device/${encodeURIComponent(mac)}/voice`),
        ]);
        if (cancelled) return;
        setCatalog(cat);
        setSelectedVoice(dev.voice || cat.default);
        setSelectedSpeed(dev.speed || cat.default_speed);
        setSelectedLength(dev.response_length || cat.default_length || "brief");
        setSelectedVolume(typeof dev.volume === "number" ? Math.max(0, Math.min(100, dev.volume)) : 80);
        setPower(coercePower({
          sleepTimeoutSec: typeof dev.sleepTimeoutSec === "number" ? dev.sleepTimeoutSec : DEFAULT_POWER.sleepTimeoutSec,
          listenScreenOff: typeof dev.listenScreenOff === "boolean" ? dev.listenScreenOff : DEFAULT_POWER.listenScreenOff,
          sleepMode: dev.sleepMode === "deep_sleep" ? "deep_sleep" : "screen_off",
        }));
        setPowerApplyState(dev.powerApplyState ?? "unset");
        setPowerSaveMessage(null);
      } catch (e) {
        if (cancelled) return;
        setLoadError(e instanceof ApiError ? e.message : "Failed to load voice settings.");
      }
    }

    load();
    return () => { cancelled = true; };
  }, [mac]);

  useEffect(() => {
    return () => {
      stopAudio();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function stopAudio() {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
  }

  async function playPreview(text?: string) {
    setPreviewStatus("loading");
    setPreviewError(null);
    stopAudio();

    try {
      const blob = await apiBinary("/voice/preview", {
        method: "POST",
        body: JSON.stringify({
          voice: selectedVoice,
          speed: selectedSpeed,
          text: text && text.trim() ? text : undefined,
        }),
      });
      const url = URL.createObjectURL(blob);
      blobUrlRef.current = url;
      const audio = new Audio(url);
      audio.volume = Math.max(0, Math.min(1, selectedVolume / 100));
      audioRef.current = audio;

      audio.onended = () => {
        setPreviewStatus("idle");
        stopAudio();
      };
      audio.onerror = () => {
        setPreviewStatus("error");
        setPreviewError("Audio playback failed.");
        stopAudio();
      };

      setPreviewStatus("playing");
      await audio.play();
    } catch (e) {
      setPreviewStatus("error");
      setPreviewError(e instanceof ApiError ? e.message : "Preview failed.");
    }
  }

  function updatePower(patch: Partial<DevicePowerSettings>) {
    setPower((prev) => {
      const next = { ...prev, ...patch };
      if (patch.sleepMode === "deep_sleep") next.listenScreenOff = false;
      if (patch.listenScreenOff === true) next.sleepMode = "screen_off";
      return coercePower(next);
    });
    setSaveStatus("idle");
    setPowerSaveMessage(null);
  }

  async function handleSave() {
    setSaveStatus("saving");
    setSaveError(null);
    try {
      const saved = await apiPut<DeviceVoice>(
        `/device/${encodeURIComponent(mac)}/voice`,
        {
          voice: selectedVoice,
          speed: selectedSpeed,
          response_length: selectedLength,
          volume: selectedVolume,
          sleepTimeoutSec: power.sleepTimeoutSec,
          listenScreenOff: power.listenScreenOff,
          sleepMode: power.sleepMode,
        },
      );
      setPowerApplyState(saved.powerApplyState ?? "unset");
      setPowerSaveMessage(saved.powerSaveMessage ?? null);
      setSaveStatus("saved");
      setTimeout(() => setSaveStatus("idle"), 2200);
    } catch (e) {
      setSaveStatus("error");
      setSaveError(e instanceof ApiError ? e.message : "Save failed.");
    }
  }

  async function handleUnbind() {
    if (unbindBusy) return;
    setUnbindBusy(true);
    setUnbindError(null);
    try {
      await apiPost(`/device/${encodeURIComponent(deviceId)}/unbind`);
      setUnbindOpen(false);
      onDeleted?.();
    } catch (e) {
      setUnbindError(e instanceof ApiError ? e.message : "Unbind failed.");
      setUnbindBusy(false);
    }
  }

  async function handleDelete() {
    if (deleteBusy) return;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await apiDelete(`/device/${encodeURIComponent(deviceId)}`);
      setDeleteOpen(false);
      onDeleted?.();
    } catch (e) {
      setDeleteError(e instanceof ApiError ? e.message : "Delete failed.");
      setDeleteBusy(false);
    }
  }

  if (loadError) {
    return (
      <div className="flex items-center gap-1.5 text-[12px] text-risk-urgent py-1">
        <AlertTriangle size={13} strokeWidth={1.75} />
        {loadError}
      </div>
    );
  }

  if (!catalog) {
    return (
      <div className="flex items-center gap-3 py-1">
        <div className="skeleton h-7 w-40 rounded-card" />
        <div className="skeleton h-7 w-28 rounded-card" />
        <div className="skeleton h-7 w-16 rounded-card" />
        <div className="skeleton h-7 w-12 rounded-card" />
      </div>
    );
  }

  const voiceSelectId = `voice-${mac}`;
  const speedSelectId = `speed-${mac}`;
  const lengthSelectId = `length-${mac}`;
  const volumeId = `volume-${mac}`;
  const testMsgId = `test-msg-${mac}`;

  const selectedVoiceObj = catalog.voices.find((v) => v.id === selectedVoice);
  const isEdgeVoice = selectedVoiceObj ? !selectedVoiceObj.local : false;
  const previewBusy = previewStatus === "loading" || previewStatus === "playing";
  const bound = Boolean(agentId);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">

        <div className="flex flex-col gap-1 min-w-0">
          <label
            htmlFor={voiceSelectId}
            className="text-[10px] uppercase tracking-[0.12em] text-slate-muted font-medium"
          >
            Voice
          </label>
          <select
            id={voiceSelectId}
            value={selectedVoice}
            onChange={(e) => {
              setSelectedVoice(e.target.value);
              setSaveStatus("idle");
            }}
            className={classNames(
              "bg-white border border-slate-line/80 rounded-card px-2.5 py-1.5",
              "text-[13px] text-slate-deep tracking-tight",
              "focus:outline-none focus:border-teal focus:ring-2 focus:ring-teal/15 transition",
              "min-w-[160px]",
            )}
            aria-label="Voice"
          >
            {catalog.voices.map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}{v.recommended ? " ★" : ""}{!v.local ? " (Edge)" : ""}
              </option>
            ))}
          </select>
          {isEdgeVoice && (
            <span className="flex items-center gap-1 text-[11px] text-slate-muted">
              <Wifi size={10} strokeWidth={1.75} />
              needs internet
            </span>
          )}
          {!isEdgeVoice && <span className="h-[16px]" aria-hidden />}
        </div>

        <div className="flex flex-col gap-1 min-w-0">
          <label
            htmlFor={speedSelectId}
            className="text-[10px] uppercase tracking-[0.12em] text-slate-muted font-medium"
          >
            Speed
          </label>
          <select
            id={speedSelectId}
            value={selectedSpeed}
            onChange={(e) => {
              setSelectedSpeed(e.target.value);
              setSaveStatus("idle");
            }}
            className={classNames(
              "bg-white border border-slate-line/80 rounded-card px-2.5 py-1.5",
              "text-[13px] text-slate-deep tracking-tight",
              "focus:outline-none focus:border-teal focus:ring-2 focus:ring-teal/15 transition",
              "min-w-[140px]",
            )}
            aria-label="Speed"
          >
            {catalog.speeds.map((s) => (
              <option key={s.id} value={s.id}>{s.label}</option>
            ))}
          </select>
          <span className="h-[16px]" aria-hidden />
        </div>

        <div className="flex flex-col gap-1 min-w-0">
          <label
            htmlFor={lengthSelectId}
            className="text-[10px] uppercase tracking-[0.12em] text-slate-muted font-medium"
          >
            Length
          </label>
          <select
            id={lengthSelectId}
            value={selectedLength}
            onChange={(e) => {
              setSelectedLength(e.target.value);
              setSaveStatus("idle");
            }}
            className={classNames(
              "bg-white border border-slate-line/80 rounded-card px-2.5 py-1.5",
              "text-[13px] text-slate-deep tracking-tight",
              "focus:outline-none focus:border-teal focus:ring-2 focus:ring-teal/15 transition",
              "min-w-[140px]",
            )}
            aria-label="Response length"
          >
            {(catalog.lengths || []).map((l) => (
              <option key={l.id} value={l.id}>{l.label}</option>
            ))}
          </select>
          <span className="h-[16px]" aria-hidden />
        </div>

        <div className="flex items-end gap-2 pb-[20px]">
          <button
            onClick={() => playPreview()}
            disabled={previewBusy}
            className={classNames(
              "btn-secondary text-[12px] px-3 py-1.5 gap-1.5",
              previewBusy && "opacity-60 pointer-events-none",
            )}
            aria-label="Preview voice"
            title="Play a short preview with the selected voice and speed"
          >
            {previewStatus === "loading" ? (
              <Loader2 size={13} className="animate-spin" strokeWidth={1.75} />
            ) : (
              <Volume2 size={13} strokeWidth={1.75} />
            )}
            {previewStatus === "playing" ? "Playing…" : previewStatus === "loading" ? "Synthesising…" : "Preview"}
          </button>

          {bound && (
            <button
              type="button"
              onClick={() => { setUnbindError(null); setUnbindOpen(true); }}
              className="btn-secondary text-[12px] px-3 py-1.5 gap-1.5"
              aria-label="Unbind this Watcher from the current client"
            >
              <Unlink2 size={13} strokeWidth={1.75} />
              Unbind
            </button>
          )}

          <button
            type="button"
            onClick={() => { setDeleteError(null); setDeleteOpen(true); }}
            className="btn-danger text-[12px] px-3 py-1.5 gap-1.5"
            aria-label="Delete / Reset Device"
          >
            <Trash2 size={13} strokeWidth={1.75} />
            Delete / Reset Device
          </button>

          <button
            onClick={handleSave}
            disabled={saveStatus === "saving"}
            className={classNames(
              "btn-primary text-[12px] px-3 py-1.5 gap-1.5",
              saveStatus === "saving" && "opacity-60 pointer-events-none",
              saveStatus === "saved" && "bg-teal-deep",
            )}
            aria-label="Save voice settings for this device"
          >
            {saveStatus === "saving" ? (
              <Loader2 size={13} className="animate-spin" strokeWidth={1.75} />
            ) : saveStatus === "saved" ? (
              <Check size={13} strokeWidth={2} />
            ) : null}
            {saveStatus === "saving" ? "Saving…" : saveStatus === "saved" ? "Saved" : "Save"}
          </button>
        </div>
      </div>

      <div className="flex flex-col gap-2 pt-1">
        <div className="text-[10px] uppercase tracking-[0.12em] text-slate-muted font-medium">
          Power / Sleep
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1 min-w-0">
            <label
              htmlFor={`sleep-timeout-${mac}`}
              className="text-[10px] uppercase tracking-[0.12em] text-slate-muted font-medium"
            >
              Sleep after inactivity
            </label>
            <select
              id={`sleep-timeout-${mac}`}
              value={power.sleepTimeoutSec}
              onChange={(e) => updatePower({ sleepTimeoutSec: Number(e.target.value) })}
              className="bg-white border border-slate-line/80 rounded-card px-2.5 py-1.5 text-[13px] text-slate-deep tracking-tight focus:outline-none focus:border-teal focus:ring-2 focus:ring-teal/15 transition min-w-[140px]"
            >
              {SLEEP_TIMEOUTS.map((t) => (
                <option key={t.sec} value={t.sec}>{t.label}</option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1 min-w-0">
            <label
              htmlFor={`listen-screen-${mac}`}
              className="text-[10px] uppercase tracking-[0.12em] text-slate-muted font-medium"
            >
              Listen while screen is off
            </label>
            <select
              id={`listen-screen-${mac}`}
              value={power.listenScreenOff ? "on" : "off"}
              onChange={(e) => updatePower({ listenScreenOff: e.target.value === "on" })}
              className="bg-white border border-slate-line/80 rounded-card px-2.5 py-1.5 text-[13px] text-slate-deep tracking-tight focus:outline-none focus:border-teal focus:ring-2 focus:ring-teal/15 transition min-w-[120px]"
            >
              <option value="on">On</option>
              <option value="off">Off</option>
            </select>
          </div>
          <div className="flex flex-col gap-1 min-w-0">
            <label
              htmlFor={`sleep-mode-${mac}`}
              className="text-[10px] uppercase tracking-[0.12em] text-slate-muted font-medium"
            >
              Sleep mode
            </label>
            <select
              id={`sleep-mode-${mac}`}
              value={power.sleepMode}
              onChange={(e) => updatePower({ sleepMode: e.target.value as DevicePowerSettings["sleepMode"] })}
              className="bg-white border border-slate-line/80 rounded-card px-2.5 py-1.5 text-[13px] text-slate-deep tracking-tight focus:outline-none focus:border-teal focus:ring-2 focus:ring-teal/15 transition min-w-[150px]"
            >
              {SLEEP_MODES.map((m) => (
                <option key={m.id} value={m.id}>{m.label}</option>
              ))}
            </select>
          </div>
        </div>
        <p className="text-[12px] text-slate-muted leading-snug max-w-2xl">
          {powerHelp(power)}
          {power.sleepMode === "deep_sleep" ? " Deep sleep disconnects Wi-Fi and voice listening until the Watcher wakes." : ""}
        </p>
        {powerApplyState === "applied" && (
          <p className="text-[12px] text-teal-deep">Applied</p>
        )}
        {powerApplyState === "pending_offline" && (
          <p className="text-[12px] text-slate-muted">Pending device reconnect</p>
        )}
        {powerApplyState === "pending_ack" && (
          <p className="text-[12px] text-slate-muted">Waiting for Watcher to apply</p>
        )}
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1 min-w-[220px] flex-1">
          <label
            htmlFor={volumeId}
            className="text-[10px] uppercase tracking-[0.12em] text-slate-muted font-medium"
          >
            Volume (%)
          </label>
          <div className="flex items-center gap-3">
            <input
              id={volumeId}
              type="range"
              min={0}
              max={100}
              value={selectedVolume}
              onChange={(e) => {
                setSelectedVolume(Number(e.target.value));
                setSaveStatus("idle");
              }}
              className="flex-1 accent-teal h-1.5 cursor-pointer"
              aria-label="Volume"
            />
            <input
              type="number"
              min={0}
              max={100}
              value={selectedVolume}
              onChange={(e) => {
                const n = Number(e.target.value);
                if (Number.isNaN(n)) return;
                setSelectedVolume(Math.max(0, Math.min(100, Math.round(n))));
                setSaveStatus("idle");
              }}
              className="input !h-8 !px-2 !text-[13px] w-16 num"
              aria-label="Volume percent"
            />
          </div>
        </div>

        <div className="flex flex-col gap-1 min-w-[240px] flex-[2]">
          <label
            htmlFor={testMsgId}
            className="text-[10px] uppercase tracking-[0.12em] text-slate-muted font-medium"
          >
            Test message
          </label>
          <input
            id={testMsgId}
            type="text"
            value={testMessage}
            onChange={(e) => setTestMessage(e.target.value)}
            className="input !h-8 !text-[13px]"
            placeholder={DEFAULT_TEST_MESSAGE}
            aria-label="Test message"
          />
        </div>

        <button
          onClick={() => playPreview(testMessage)}
          disabled={previewBusy}
          className={classNames(
            "btn-secondary text-[12px] px-3 py-1.5 gap-1.5 mb-0.5",
            previewBusy && "opacity-60 pointer-events-none",
          )}
          aria-label="Test voice with custom message"
        >
          {previewStatus === "loading" ? (
            <Loader2 size={13} className="animate-spin" strokeWidth={1.75} />
          ) : (
            <Play size={13} strokeWidth={1.75} />
          )}
          Test Voice
        </button>
      </div>

      {!previewError && !saveError && previewStatus === "idle" && saveStatus === "idle" && (
        <div className="flex items-center gap-1.5 text-[12px] text-teal-deep">
          <Check size={12} strokeWidth={2} />
          {powerSaveMessage || "Voice settings ready. Click Test Voice to hear a sample."}
        </div>
      )}
      {saveStatus === "saved" && powerSaveMessage && (
        <div className="flex items-center gap-1.5 text-[12px] text-teal-deep">
          <Check size={12} strokeWidth={2} />
          {powerSaveMessage}
        </div>
      )}

      {previewError && (
        <div className="flex items-center gap-1.5 text-[12px] text-risk-urgent">
          <AlertTriangle size={12} strokeWidth={1.75} />
          {previewError}
        </div>
      )}
      {saveError && (
        <div className="flex items-center gap-1.5 text-[12px] text-risk-urgent">
          <AlertTriangle size={12} strokeWidth={1.75} />
          {saveError}
        </div>
      )}

      <Modal
        open={unbindOpen}
        onClose={() => { if (!unbindBusy) setUnbindOpen(false); }}
        title="Unbind this Watcher?"
        size="md"
        footer={
          <>
            <button
              onClick={() => setUnbindOpen(false)}
              disabled={unbindBusy}
              className="btn-secondary"
            >
              Cancel
            </button>
            <button onClick={handleUnbind} disabled={unbindBusy} className="btn-primary">
              {unbindBusy && <Loader2 size={14} className="animate-spin" />}
              <Unlink2 size={14} />
              Unbind
            </button>
          </>
        }
      >
        <div className="flex items-start gap-3 text-[14px] text-slate-deep leading-relaxed">
          <Unlink2 size={18} className="text-teal-deep shrink-0 mt-0.5" />
          <p>
            This will remove the Watcher from the current client and return it to the unbound device pool. The client/person will not be deleted.
          </p>
        </div>
        {unbindError && (
          <div className="mt-4 text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
            {unbindError}
          </div>
        )}
      </Modal>

      <Modal
        open={deleteOpen}
        onClose={() => { if (!deleteBusy) setDeleteOpen(false); }}
        title="Delete / Reset Device?"
        size="md"
        footer={
          <>
            <button
              onClick={() => setDeleteOpen(false)}
              disabled={deleteBusy}
              className="btn-secondary"
            >
              Cancel
            </button>
            <button onClick={handleDelete} disabled={deleteBusy} className="btn-danger">
              {deleteBusy && <Loader2 size={14} className="animate-spin" />}
              <Trash2 size={14} />
              Delete / Reset Device
            </button>
          </>
        }
      >
        <div className="flex items-start gap-3 text-[14px] text-slate-deep leading-relaxed">
          <AlertTriangle size={18} className="text-risk-urgent shrink-0 mt-0.5" />
          <p>
            This will remove the Watcher from CareConnect. The client/person will not be deleted. If the Watcher reconnects, Nexus will automatically register it again as an unbound device.
          </p>
        </div>
        {deleteError && (
          <div className="mt-4 text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
            {deleteError}
          </div>
        )}
      </Modal>
    </div>
  );
}
