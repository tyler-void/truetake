"""Callouts: config validation, the scrolling screen they follow, the numbers rule and the time expressions."""
import pytest

from truetake import callouts, config, screen
from truetake.config import ConfigError

BASE = """version: 1
output: demo.mp4
voice: {engine: none}
usage_prices: {per_mtok: {input: 2, output: 10, cache_write_5m: 2.5, cache_write_1h: 4, cache_read: 0.1}}
slides: {pdf: slides.pdf}
segments:
  - {id: title, kind: title, title: Same task, subtitle: one run}
  - {id: stats, kind: slide, page: 1}
  - {id: a, kind: scene, cwd: /tmp/a, prompt: P, annotate: {usage_bar: true}}
  - {id: b, kind: scene, cwd: /tmp/b, prompt: P}
  - {id: both, kind: pair, left: a, right: b}
"""


def load(tmp_path, callouts_yaml):
    p = tmp_path / "truetake.yaml"
    p.write_text(BASE + callouts_yaml, encoding="utf-8")
    return config.load(p)


def test_callouts_parse(tmp_path):
    cfg = load(tmp_path, """callouts:
  output: demo-cap.mp4
  items:
    - {beat: "0:00", from: title, to: stats, text: The task, boxes: [{segment: title, text: title}]}
    - {from: stats, to: "both", text: The layer, boxes: [{segment: stats, rect: [10, 20, 300, 80]}]}
    - from: both
      to: "a@reply"
      text: Same steps
      boxes: [{scene: a, status: true}, {scene: b, line: '^● Bash', rows: 2}]
      zoom: 1.5
""")
    co = cfg.callouts
    assert co.output.name == "demo-cap.mp4" and len(co.items) == 3
    assert co.items[1].boxes[0].rect == (10, 20, 300, 80)
    assert co.items[2].boxes[1].rows == 2 and co.items[2].zoom == 1.5 and co.items[2].zoom_seconds == 8


@pytest.mark.parametrize("item, match", [
    ("{from: title, to: stats, text: one two three four five six seven eight nine}", "9 words"),
    ("{from: title, to: nowhere, text: T}", "no segment"),
    ("{from: 'title+x', to: stats, text: T}", "not a time"),
    ("{from: 'stats@reply', to: both, text: T}", "not a scene"),
    ("{from: title, to: stats, text: T, boxes: [{segment: a, rect: [0, 0, 1, 1]}]}", "title and slide"),
    ("{from: title, to: stats, text: T, boxes: [{segment: title, scene: a, line: x}]}", "exactly one"),
    ("{from: title, to: stats, text: T, boxes: [{scene: b, status: true}]}", "no usage bar"),
    ("{from: title, to: stats, text: T, boxes: [{segment: title, text: title}], zoom: 2}", "magnifies a terminal-line"),
    ("{from: title, to: stats, text: T, boxes: [{scene: a, line: '('}]}", "not a valid regex"),
])
def test_callouts_refuse(tmp_path, item, match):
    with pytest.raises(ConfigError, match=match):
        load(tmp_path, f"callouts:\n  output: demo-cap.mp4\n  items:\n    - {item}\n")


def test_callouts_never_overwrite_the_video(tmp_path):
    with pytest.raises(ConfigError, match="never overwrite"):
        load(tmp_path, "callouts:\n  output: demo.mp4\n  items:\n    - {from: title, to: stats, text: T}\n")


def test_snapshots_follow_the_scroll():
    events = [[0.0, "o", "one\r\ntwo\r\n"], [1.0, "o", "three\r\n"], [2.0, "o", "four"]]
    snaps = dict(screen.snapshots(events, rows=3, cols=10))
    assert snaps[0.0] == ["one", "two", ""]
    assert snaps[1.0] == ["two", "three", ""]  # "one" scrolled off the top
    assert snaps[2.0] == ["two", "three", "four"]


def test_snapshots_wrap_long_lines():
    snaps = screen.snapshots([[0.0, "o", "abcdefgh"]], rows=3, cols=5)
    assert snaps[-1][1] == ["abcde", "fgh", ""]


@pytest.mark.parametrize("text, shown, missing", [
    ("rtk gain claims 0 tokens", "rtk gain says saved  0 tokens", []),
    ("rtk gain claims 0 tokens", "saved 10 tokens", ["0"]),  # 0 inside 10 does not count
    ("FULL costs $0.163", "session cost $0.163", []),
    ("One run each", "One pair of runs.", []),
    ("One run each", "a pair of runs", ["One"]),
    ("Two of 1 runs", "2 setups, 1 run", []),  # "Two" is backed by the digit 2
    ("Same steps, FULL climbs faster", "anything", []),
])
def test_caption_numbers_must_be_on_screen(text, shown, missing):
    assert callouts.check_numbers(text, shown)[1] == missing


def test_piecewise_expression():
    spans = [callouts.Span(0, 1, (10, 0, 0, 0)), callouts.Span(1, 2, (20, 0, 0, 0)), callouts.Span(2, 3, (30, 0, 0, 0))]
    assert callouts._expr(spans, lambda s: s.rect[0]) == "if(lt(t,1.000),10,if(lt(t,2.000),20,30))"


def test_merge_joins_equal_neighbours():
    r = (1, 2, 3, 4)
    merged = callouts._merge([callouts.Span(0, 1, r), callouts.Span(1, 2, r), callouts.Span(2, 3, (0, 0, 1, 1))])
    assert [(s.t0, s.t1) for s in merged] == [(0, 2), (2, 3)]


def test_segment_end_meets_the_next_start(tmp_path):
    # assemble.json rounds `start` to the millisecond but keeps `seconds` exact (here 866 frames at 24 fps)
    cfg = load(tmp_path, "")
    tl = callouts.Timeline.__new__(callouts.Timeline)
    tl.cfg = cfg
    tl.segs = {"title": {"start": 13.5, "seconds": 36.083333333333336}, "stats": {"start": 49.583, "seconds": 6.375}}
    assert tl.time("title:end") == tl.time("stats")
