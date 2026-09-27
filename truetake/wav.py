"""Read 16-bit PCM out of a WAV (RIFF) container, keeping only the `data` chunk.

Some TTS services return a complete WAV file with extra chunks after the audio, such as a `C2PA`
provenance manifest. Treating those bytes as samples produces a click at the start (the 44-byte header) and
a full-scale burst of noise at the end (the manifest). Only the `data` chunk is audio.
"""
from __future__ import annotations

import struct
import wave
from pathlib import Path


class WavError(ValueError):
    pass


def decode(data: bytes, default_rate: int = 24000) -> tuple[bytes, int, int, bytes]:
    """Return (pcm, sample_rate, channels, extra_chunks).

    Bytes that are not a RIFF/WAVE file are taken as raw 16-bit mono PCM at `default_rate`. Every chunk other
    than `fmt ` and `data` is returned, whole, in `extra_chunks` so it can be kept as evidence. The walk
    stops at the end of the bytes, so a truncated file still yields the audio it has.
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data[: len(data) // 2 * 2], default_rate, 1, b""
    i, rate, channels, pcm, extra = 12, default_rate, 1, None, b""
    while i + 8 <= len(data):
        cid, size = data[i:i + 4], struct.unpack("<I", data[i + 4:i + 8])[0]
        body = data[i + 8:i + 8 + size]
        if cid == b"fmt ":
            if len(body) < 16:
                raise WavError("fmt chunk is too short")
            tag, channels, rate, _, _, bits = struct.unpack("<HHIIHH", body[:16])
            if tag != 1 or bits != 16:
                raise WavError(f"only 16-bit PCM is supported (format tag {tag}, {bits} bits)")
        elif cid == b"data":
            pcm = body
        else:
            extra += data[i:i + 8 + size]
        i += 8 + size + (size & 1)  # chunks are padded to an even length
    if pcm is None:
        raise WavError("RIFF file without a data chunk")
    align = 2 * channels
    return pcm[: len(pcm) // align * align], rate, channels, extra


def write(path: Path, pcm: bytes, rate: int, channels: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
