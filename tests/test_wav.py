import struct

import pytest

from truetake import wav


def riff(pcm: bytes, rate: int = 24000, channels: int = 1, bits: int = 16, extra: list[tuple[bytes, bytes]] = ()) -> bytes:
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * channels * bits // 8, channels * bits // 8, bits)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm
    for cid, body in extra:
        chunks += cid + struct.pack("<I", len(body)) + body + (b"\0" if len(body) % 2 else b"")
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


PCM = struct.pack("<8h", 0, 100, -100, 2000, -2000, 50, -50, 0)


def test_plain_riff_returns_only_samples():
    pcm, rate, ch, extra = wav.decode(riff(PCM, rate=22050))
    assert pcm == PCM and rate == 22050 and ch == 1 and extra == b""


def test_trailing_chunk_is_not_audio():
    # A provenance manifest after the data chunk must never be read as samples.
    manifest = b"\xff" * 601  # odd size: the chunk is padded to an even length
    data = riff(PCM, extra=[(b"C2PA", manifest)])
    pcm, rate, ch, extra = wav.decode(data)
    assert pcm == PCM
    assert extra.startswith(b"C2PA") and manifest in extra


def test_header_is_not_audio():
    pcm, *_ = wav.decode(riff(PCM))
    assert b"RIFF" not in pcm and b"fmt " not in pcm


def test_chunk_before_data_is_kept_as_extra():
    data = riff(PCM, extra=[])
    # insert a LIST chunk between fmt and data
    i = data.index(b"data")
    listchunk = b"LIST" + struct.pack("<I", 4) + b"INFO"
    body = data[12:i] + listchunk + data[i:]
    data = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body
    pcm, _, _, extra = wav.decode(data)
    assert pcm == PCM and extra == listchunk


def test_raw_pcm_passes_through():
    pcm, rate, ch, extra = wav.decode(PCM + b"\x01", default_rate=16000)
    assert pcm == PCM and rate == 16000 and extra == b""


def test_truncated_data_chunk_keeps_what_is_there():
    data = riff(PCM)[:-4]
    pcm, *_ = wav.decode(data)
    assert pcm == PCM[:-4]


def test_non_pcm16_is_refused():
    with pytest.raises(wav.WavError):
        wav.decode(riff(PCM, bits=8))


def test_no_data_chunk_is_refused():
    fmt = struct.pack("<HHIIHH", 1, 1, 24000, 48000, 2, 16)
    body = b"fmt " + struct.pack("<I", 16) + fmt
    with pytest.raises(wav.WavError):
        wav.decode(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)


def test_write_then_decode_round_trip(tmp_path):
    p = tmp_path / "x.wav"
    wav.write(p, PCM, 24000)
    pcm, rate, *_ = wav.decode(p.read_bytes())
    assert pcm == PCM and rate == 24000
