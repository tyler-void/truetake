import pytest

from truetake.config import ConfigError, load

SHA = "a" * 40


def write(tmp_path, body: str):
    p = tmp_path / "truetake.yaml"
    p.write_text(body)
    return p


SCENE = f"""
segments:
  - id: s1
    kind: scene
    cwd: sandbox
    prompt: Do the thing.
    card: {{commit: {SHA}, file: a.py, lines: [[1, 3], [7, 9]], explain: What it does}}
    captions: [{{text: One, at: "@reply"}}]
    narration: {{card: About the card., main: About the take.}}
"""


def test_loads_a_scene(tmp_path):
    cfg = load(write(tmp_path, SCENE))
    s = cfg.scenes()[0]
    assert s.cwd == (tmp_path / "sandbox").resolve()
    assert s.card.lines == [(1, 3), (7, 9)] and s.card.repo == s.cwd
    assert s.card_narration == "About the card." and s.narration == "About the take."
    assert cfg.voice.engine == "kokoro" and cfg.voice.loudness_lufs == -16


def test_card_commit_must_be_pinned(tmp_path):
    with pytest.raises(ConfigError, match="40-character"):
        load(write(tmp_path, SCENE.replace(SHA, "HEAD")))


def test_overlapping_ranges_are_refused(tmp_path):
    with pytest.raises(ConfigError):
        load(write(tmp_path, SCENE.replace("[[1, 3], [7, 9]]", "[[1, 5], [4, 9]]")))


def test_duplicate_ids_are_refused(tmp_path):
    body = "segments:\n  - {id: a, kind: title, title: A}\n  - {id: a, kind: title, title: B}\n"
    with pytest.raises(ConfigError):
        load(write(tmp_path, body))


def test_slide_needs_a_pdf(tmp_path):
    with pytest.raises(ConfigError, match="slides.pdf"):
        load(write(tmp_path, "segments:\n  - {id: a, kind: slide, page: 1}\n"))


def test_card_narration_needs_a_card(tmp_path):
    body = "segments:\n  - {id: a, kind: scene, cwd: x, prompt: p, narration: {card: hi}}\n"
    with pytest.raises(ConfigError):
        load(write(tmp_path, body))
