"""careconnect multi-engine TTS server (Piper + Kokoro + Edge).

Drop-in superset of the old piper_tts_server: serves the same
POST /v1/audio/speech {input, voice, response_format} -> WAV, but `voice`
selects the engine via an "engine:name" id:

    kokoro:af_heart            -> Kokoro (local neural, 24 kHz native)   [DEFAULT]
    edge:en-US-AvaNeural       -> Microsoft Edge neural (needs internet)
    piper:en_US-hfc_female-medium -> Piper (local)
    en_US-amy-medium           -> bare name = piper (back-compat)

Returns WAV bytes (each engine's native rate); the xiaozhi-server util layer
does the final loudnorm + 24 kHz + opus. Also exposes:
    GET /health   -> engine availability
    GET /voices   -> the curated voice catalog (for the dashboard selector)

Env:
    PIPER_VOICE_DIR   (default ~/.local/share/careconnect/piper-voices)
    KOKORO_DIR        (default ~/.local/share/careconnect/kokoro)
    CC_TTS_DEFAULT_VOICE (default kokoro:af_heart)
"""
from __future__ import annotations
import io
import os
import asyncio
import subprocess
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

PIPER_VOICE_DIR = Path(os.environ.get("PIPER_VOICE_DIR", str(Path.home() / ".local/share/careconnect/piper-voices")))
KOKORO_DIR = Path(os.environ.get("KOKORO_DIR", str(Path.home() / ".local/share/careconnect/kokoro")))
DEFAULT_VOICE = os.environ.get("CC_TTS_DEFAULT_VOICE", "kokoro:af_heart")

# Curated catalog surfaced to the dashboard selector (the 2026-06-03 keepers).
VOICE_CATALOG = [
    {"id": "kokoro:af_heart",               "label": "Hazel — Heart (Kokoro)",  "engine": "kokoro", "local": True,  "recommended": True},
    {"id": "edge:en-US-AvaNeural",          "label": "Ava (Edge neural)",       "engine": "edge",   "local": False, "recommended": True},
    {"id": "edge:en-US-JennyNeural",        "label": "Jenny (Edge neural)",     "engine": "edge",   "local": False, "recommended": True},
    {"id": "edge:en-US-EmmaNeural",         "label": "Emma (Edge neural)",      "engine": "edge",   "local": False, "recommended": False},
    {"id": "piper:en_US-hfc_female-medium", "label": "Clara (Piper hfc female)","engine": "piper",  "local": True,  "recommended": True},
]

_piper_voices: dict[str, object] = {}
_kokoro = None
_kokoro_err = None


def _piper(name: str):
    if name not in _piper_voices:
        from piper.voice import PiperVoice
        onnx = PIPER_VOICE_DIR / f"{name}.onnx"
        if not onnx.exists():
            raise FileNotFoundError(f"piper voice not found: {onnx}")
        _piper_voices[name] = PiperVoice.load(str(onnx))
    return _piper_voices[name]


def _kokoro_model():
    global _kokoro, _kokoro_err
    if _kokoro is None and _kokoro_err is None:
        try:
            from kokoro_onnx import Kokoro
            _kokoro = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))
        except Exception as e:  # noqa
            _kokoro_err = str(e)
    if _kokoro is None:
        raise RuntimeError(f"kokoro unavailable: {_kokoro_err}")
    return _kokoro


def _split(voice: str):
    if ":" in voice:
        eng, name = voice.split(":", 1)
        return eng, name
    return "piper", voice  # bare name = piper (back-compat)


# Speech speed: "slow" (gentle, good for elderly), "normal", "fast". Mapped
# per-engine so it actually slows the speech (NOT sample rate, which doesn't).
SPEED_PRESETS = {
    "slow":   {"kokoro": 0.80, "edge": "-25%", "piper": 1.30},
    "normal": {"kokoro": 1.00, "edge": "+0%",  "piper": 1.00},
    "fast":   {"kokoro": 1.20, "edge": "+20%", "piper": 0.85},
}


def _speed(engine: str, speed):
    # Tolerate non-string speed (older/drifted clients send numerics like 1) —
    # anything that isn't a known preset string falls back to "normal".
    if not isinstance(speed, str):
        speed = "normal"
    return SPEED_PRESETS.get(speed or "normal", SPEED_PRESETS["normal"])[engine]


def _wav_from_piper(name: str, text: str, speed: str) -> bytes:
    import wave
    from piper.voice import SynthesisConfig
    buf = io.BytesIO()
    voice = _piper(name)
    try:
        cfg = SynthesisConfig(length_scale=_speed("piper", speed))
        with wave.open(buf, "wb") as w:
            voice.synthesize_wav(text, w, syn_config=cfg)
    except Exception:
        # older piper signature without syn_config
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            voice.synthesize_wav(text, w)
    return buf.getvalue()


def _wav_from_kokoro(name: str, text: str, speed: str) -> bytes:
    import soundfile as sf
    samples, sr = _kokoro_model().create(text, voice=name, speed=_speed("kokoro", speed), lang="en-us")
    buf = io.BytesIO()
    sf.write(buf, samples, sr, format="WAV")
    return buf.getvalue()


def _wav_from_edge(name: str, text: str, speed: str) -> bytes:
    import edge_tts
    mp3 = io.BytesIO()

    async def go():
        async for chunk in edge_tts.Communicate(text, name, rate=_speed("edge", speed)).stream():
            if chunk["type"] == "audio":
                mp3.write(chunk["data"])
    asyncio.run(go())
    # transcode mp3 -> wav 24k mono. Write to a seekable temp file so ffmpeg
    # fills the RIFF size header correctly (pipe output leaves a bogus size).
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav") as tf:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", "pipe:0",
             "-ar", "24000", "-ac", "1", tf.name],
            input=mp3.getvalue(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return Path(tf.name).read_bytes()


def synth(voice: str, text: str, speed: str = "normal") -> bytes:
    eng, name = _split(voice)
    if eng == "piper":
        return _wav_from_piper(name, text, speed)
    if eng == "kokoro":
        return _wav_from_kokoro(name, text, speed)
    if eng == "edge":
        return _wav_from_edge(name, text, speed)
    raise ValueError(f"unknown engine: {eng}")


app = FastAPI()


class SpeakRequest(BaseModel):
    input: str
    voice: str | None = None
    # Accept str ("slow"/"normal"/"fast"). Also tolerate numeric speed from
    # older drifted clients (e.g. 1) instead of 422-ing — _speed() coerces it.
    speed: str | int | float | None = "normal"   # slow | normal | fast
    response_format: str | None = "wav"


@app.get("/health")
def health():
    engines = {"piper": True, "kokoro": False, "edge": False}
    try:
        _kokoro_model(); engines["kokoro"] = True
    except Exception:
        engines["kokoro"] = False
    try:
        import edge_tts  # noqa
        engines["edge"] = True
    except Exception:
        engines["edge"] = False
    return {"ok": True, "default_voice": DEFAULT_VOICE, "engines": engines,
            "piper_voices_loaded": sorted(_piper_voices.keys())}


SPEED_OPTIONS = [
    {"id": "slow",   "label": "Slow (gentle, for elderly)"},
    {"id": "normal", "label": "Normal"},
    {"id": "fast",   "label": "Fast"},
]

# Response length (verbosity) — applied by the xiaozhi-server (prompt + token cap),
# surfaced here so the dashboard reads one catalog for all per-device options.
LENGTH_OPTIONS = [
    {"id": "brief",    "label": "Brief (1–2 sentences — recommended)"},
    {"id": "normal",   "label": "Normal (a short paragraph)"},
    {"id": "detailed", "label": "Detailed (fuller answers)"},
]


@app.get("/voices")
def voices():
    return {"default": DEFAULT_VOICE, "voices": VOICE_CATALOG,
            "speeds": SPEED_OPTIONS, "default_speed": "normal",
            "lengths": LENGTH_OPTIONS, "default_length": "brief"}


@app.post("/v1/audio/speech")
def speak(req: SpeakRequest):
    voice = req.voice or DEFAULT_VOICE
    try:
        wav = synth(voice, req.input, req.speed or "normal")
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "voice": voice})
    return Response(content=wav, media_type="audio/wav",
                    headers={"Content-Disposition": "inline; filename=speech.wav"})
