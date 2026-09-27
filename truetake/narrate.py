"""narrate: one clip per narration string, written by the configured TTS engine."""
from __future__ import annotations

from pathlib import Path

from . import state, tts
from .config import Config, Segment


def clips(cfg: Config, seg: Segment) -> list[tuple[str, Path, str]]:
    """(role, wav path, text) for each narration string of a segment, in speaking order."""
    d = state.tts_dir(cfg)
    out = []
    if seg.kind == "scene" and seg.card_narration:
        out.append(("card", d / f"{seg.id}.card.wav", seg.card_narration))
    if seg.narration:
        out.append(("main", d / f"{seg.id}.wav", seg.narration))
    return out


def narrate(cfg: Config, only: list[str] | None = None, log=print) -> None:
    if cfg.voice.engine == "none":
        log("  voice.engine is none: no narration")
        return
    eng = None
    for seg in cfg.segments:
        if only and seg.id not in only:
            continue
        for role, path, text in clips(cfg, seg):
            if tts.up_to_date(path, text):
                log(f"  {seg.id} ({role}): up to date")
                continue
            eng = eng or tts.engine(cfg.voice.engine, cfg.voice.name, cfg.voice.gemini_model)
            tts.synthesize(eng, text, path)
            log(f"  {seg.id} ({role}): written")
