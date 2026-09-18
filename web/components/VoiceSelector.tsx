"use client";

/**
 * VoiceSelector — per-device voice + speed picker.
 *
 * Renders two <select> controls (Voice, Speed) pre-populated from
 * GET /api/voices and pre-selected from GET /api/device/{mac}/voice.
 * Includes a Preview button (POST /api/voice/preview → audio/wav blob)
 * and a Save button (PUT /api/device/{mac}/voice).
 *
 * Designed as a self-contained component so it can be embedded inline
 * inside a table expander row or a mobile card without lifting state.
 */

import { useEffect, useRef, useState } from "react";
import { Volume2, Loader2, Check, AlertTriangle, Wifi } from "lucide-react";
import { apiGet, apiPut, apiBinary, ApiError } from "@/lib/api";
import { classNames } from "@/lib/format";
import type { VoiceCatalog, DeviceVoice } from "@/lib/types";

// ── Types ─────────────────────────────────────────────────────────────────────

interface Props {
  mac: string;
}

type SaveStatus = "idle" | "saving" | "saved" | "error";
type PreviewStatus = "idle" | "loading" | "playing" | "error";

// ── Component ─────────────────────────────────────────────────────────────────

export function VoiceSelector({ mac }: Props) {
  const [catalog, setCatalog] = useState<VoiceCatalog | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Currently selected values — initialised from device's current settings.
  const [selectedVoice, setSelectedVoice] = useState<string>("");
  const [selectedSpeed, setSelectedSpeed] = useState<string>("");
  const [selectedLength, setSelectedLength] = useState<string>("");

  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);

  const [previewStatus, setPreviewStatus] = useState<PreviewStatus>("idle");
  const [previewError, setPreviewError] = useState<string | null>(null);

  // Audio element ref so we can stop a previous preview before starting a new one.
  const audioRef = useRef<HTMLAudioElement | null>(null);
  // Object URL cleanup.
  const blobUrlRef = useRef<string | null>(null);

  // ── Load catalog + device settings ──────────────────────────────────────────

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
      } catch (e) {
        if (cancelled) return;
        setLoadError(e instanceof ApiError ? e.message : "Failed to load voice settings.");
      }
    }

    load();
    return () => { cancelled = true; };
  }, [mac]);

  // ── Cleanup blob URL on unmount ──────────────────────────────────────────────

  useEffect(() => {
    return () => {
      stopAudio();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Helpers ──────────────────────────────────────────────────────────────────

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

  async function handlePreview() {
    setPreviewStatus("loading");
    setPreviewError(null);
    stopAudio();

    try {
      const blob = await apiBinary("/voice/preview", {
        method: "POST",
        body: JSON.stringify({ voice: selectedVoice, speed: selectedSpeed }),
      });
      const url = URL.createObjectURL(blob);
      blobUrlRef.current = url;
      const audio = new Audio(url);
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

  async function handleSave() {
    setSaveStatus("saving");
    setSaveError(null);
    try {
      await apiPut<DeviceVoice>(
        `/device/${encodeURIComponent(mac)}/voice`,
        { voice: selectedVoice, speed: selectedSpeed, response_length: selectedLength },
      );
      setSaveStatus("saved");
      // Reset back to idle after a short beat so the checkmark is visible.
      setTimeout(() => setSaveStatus("idle"), 2200);
    } catch (e) {
      setSaveStatus("error");
      setSaveError(e instanceof ApiError ? e.message : "Save failed.");
    }
  }

  // ── Render: load error ───────────────────────────────────────────────────────

  if (loadError) {
    return (
      <div className="flex items-center gap-1.5 text-[12px] text-risk-urgent py-1">
        <AlertTriangle size={13} strokeWidth={1.75} />
        {loadError}
      </div>
    );
  }

  // ── Render: skeleton while loading ───────────────────────────────────────────

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

  // ── Render: controls ─────────────────────────────────────────────────────────

  const voiceSelectId = `voice-${mac}`;
  const speedSelectId = `speed-${mac}`;
  const lengthSelectId = `length-${mac}`;

  const selectedVoiceObj = catalog.voices.find((v) => v.id === selectedVoice);
  const isEdgeVoice = selectedVoiceObj ? !selectedVoiceObj.local : false;

  return (
    <div className="flex flex-wrap items-center gap-3">

      {/* Voice dropdown */}
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
        {/* "needs internet" hint shown below the select when an Edge voice is chosen */}
        {isEdgeVoice && (
          <span className="flex items-center gap-1 text-[11px] text-slate-muted">
            <Wifi size={10} strokeWidth={1.75} />
            needs internet
          </span>
        )}
      </div>

      {/* Speed dropdown */}
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
        {/* Spacer so Speed column aligns with Voice even without the hint. */}
        {!isEdgeVoice && <span className="h-[16px]" aria-hidden />}
      </div>

      {/* Response length (verbosity) — "brief" keeps replies short for elderly users. */}
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
        {!isEdgeVoice && <span className="h-[16px]" aria-hidden />}
      </div>

      {/* Action buttons — align to bottom of the label+select+hint stack */}
      <div className="flex items-end gap-2 pb-[20px]">

        {/* Preview */}
        <button
          onClick={handlePreview}
          disabled={previewStatus === "loading" || previewStatus === "playing"}
          className={classNames(
            "btn-secondary text-[12px] px-3 py-1.5 gap-1.5",
            (previewStatus === "loading" || previewStatus === "playing") && "opacity-60 pointer-events-none",
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

        {/* Save */}
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

      {/* Inline error messages */}
      {previewError && (
        <div className="basis-full flex items-center gap-1.5 text-[12px] text-risk-urgent mt-0.5">
          <AlertTriangle size={12} strokeWidth={1.75} />
          {previewError}
        </div>
      )}
      {saveError && (
        <div className="basis-full flex items-center gap-1.5 text-[12px] text-risk-urgent mt-0.5">
          <AlertTriangle size={12} strokeWidth={1.75} />
          {saveError}
        </div>
      )}
    </div>
  );
}
