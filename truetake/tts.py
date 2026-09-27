"""Narration clips. One clip per narration string; each WAV has a .txt beside it naming the exact text it
speaks, so a clip whose text no longer matches truetake.yaml is regenerated (Kokoro) or refused (Gemini).

Backends:
  kokoro  local, default. `pip install "truetake[kokoro]"`; the model downloads on first use.
  gemini  optional, over the network. The key comes from $GEMINI_API_KEY and is never written anywhere.
  none    no narration; every segment lasts as long as its visual.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import wav

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"


class TTSError(RuntimeError):
    pass


def up_to_date(wav_path: Path, text: str) -> bool:
    txt = wav_path.with_suffix(".txt")
    return wav_path.exists() and txt.exists() and txt.read_text(encoding="utf-8") == text


def _mark(wav_path: Path, text: str) -> None:
    wav_path.with_suffix(".txt").write_text(text, encoding="utf-8")


class Kokoro:
    def __init__(self, voice: str):
        try:
            from kokoro import KPipeline
        except ImportError as e:
            raise TTSError('Kokoro is not installed: pip install "truetake[kokoro]" (see docs/pitfalls.md for '
                           "the spaCy English model it needs)") from e
        self.voice = voice
        # The first letter of a Kokoro voice is its language: a = American English, b = British English.
        self.pipe = KPipeline(lang_code=voice[0], repo_id="hexgrad/Kokoro-82M")

    def synth(self, text: str, out: Path) -> None:
        import numpy as np

        chunks = [r.audio.numpy() for r in self.pipe(text, voice=self.voice)]
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype="float32")
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        wav.write(out, pcm, 24000)


class Gemini:
    def __init__(self, voice: str, model: str | None):
        self.key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not self.key:
            raise TTSError("voice.engine is gemini but $GEMINI_API_KEY is not set")
        self.voice = voice
        self.model = model or self._newest_tts_model()

    def _call(self, url: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                     headers={"x-goog-api-key": self.key, "Content-Type": "application/json"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 503) and attempt < 4:
                    time.sleep(10 * (attempt + 1))
                    continue
                raise TTSError(f"Gemini HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e
        raise TTSError("Gemini: out of retries")

    def _newest_tts_model(self) -> str:
        names = [m["name"].removeprefix("models/") for m in self._call(f"{GEMINI_API}/models?pageSize=1000").get("models", [])]
        tts = sorted(n for n in names if "tts" in n)
        if not tts:
            raise TTSError("no Gemini TTS model is available to this key")
        return (sorted(n for n in tts if "flash" in n and "lite" not in n) or tts)[-1]

    def synth(self, text: str, out: Path) -> None:
        body = {"contents": [{"parts": [{"text": text}]}],
                "generationConfig": {"responseModalities": ["AUDIO"],
                                     "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.voice}}}}}
        r = self._call(f"{GEMINI_API}/models/{self.model}:generateContent", body)
        part = r["candidates"][0]["content"]["parts"][0]["inlineData"]
        m = re.search(r"rate=(\d+)", part.get("mimeType", ""))
        pcm, rate, channels, extra = wav.decode(base64.b64decode(part["data"]), int(m.group(1)) if m else 24000)
        wav.write(out, pcm, rate, channels)
        if extra:  # e.g. a C2PA provenance manifest: kept as evidence, never played
            out.with_suffix(".chunks").write_bytes(extra)


def engine(name: str, voice: str, gemini_model: str | None = None):
    if name == "kokoro":
        return Kokoro(voice)
    if name == "gemini":
        return Gemini(voice, gemini_model)
    raise TTSError(f"no TTS engine {name!r}")


def synthesize(eng, text: str, out: Path) -> bool:
    """Make `out` speak `text`. Returns False when the clip was already up to date."""
    if up_to_date(out, text):
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    eng.synth(text, out)
    _mark(out, text)
    return True
