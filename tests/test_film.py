"""The film set: springs as pure functions, the script's honesty rules, the config, and the cue export."""
import json

import pytest

from truetake.config import ConfigError, load
from truetake.film import script as sc
from truetake.film import spec
from truetake.film.motion import Spring
from truetake.scan import Scanner


def test_spring_is_a_pure_function_of_time():
    s = Spring(w=6.0)
    s.to(1.0, 10.0)
    s.to(2.0, 4.0)
    assert s.at(0.5) == 0.0  # before the first move
    forward = [s.at(t / 10) for t in range(60)]
    backward = [s.at(t / 10) for t in reversed(range(60))][::-1]
    assert forward == backward  # any frame, in any order
    assert s.at(30.0) == pytest.approx(4.0)  # settles on the last target


def test_glide_follows_a_steady_move_with_a_fixed_lag():
    s = Spring(w=20.0)
    s.glide(0.0, 10.0, 100.0)  # 10 units a second
    assert s.at(5.0) == pytest.approx(50.0 - 10.0 * 2 / 20.0, abs=1e-6)  # lag 2/w
    assert s.at(30.0) == pytest.approx(100.0)


def codex(tmp_path, events):
    p = tmp_path / "transcript-01.ndjson"
    p.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return sc.codex_transcript(p)


def ev(sec, kind, text="", **detail):
    return {"timestamp": f"2026-10-29T05:34:{sec:06.3f}Z", "type": kind, "text": text, "detail": detail or None}


def test_codex_items_are_verbatim_and_changes_are_counted(tmp_path):
    src = codex(tmp_path, [
        ev(10, "ITEM.COMPLETED:AGENT_MESSAGE", "I'll check the checkout."),
        ev(11, "ITEM.STARTED:COMMAND_EXECUTION", input="/bin/zsh -lc 'ls /home/ada/work'"),
        ev(12, "ITEM.COMPLETED:COMMAND_EXECUTION", output="a\nb\nc\nd\ne\n"),
        ev(13, "ITEM.STARTED:MCP_TOOL_CALL", name="mtm_team/report_progress"),
        ev(14.5, "ITEM.COMPLETED:MCP_TOOL_CALL", name="mtm_team/report_progress"),
    ])
    s = sc.build(src, cols=80, home="/home/ada")
    texts = [l.text for l in s.lines if l.text]
    assert texts[0] == "• I'll check the checkout."
    assert "$ ls ~/work" in texts  # wrapper dropped, home as ~
    assert texts[texts.index("$ ls ~/work") + 1:][:4] == ["a", "b", "c", "… 2 more lines"]
    assert "✓ mtm_team/report_progress returned after 1.5s" in texts  # the source's own timing
    assert s.transforms == {"home": 1, "shell_wrap": 1, "elided": 1, "hidden": 0, "tabs": 0}


def test_an_output_with_a_private_name_is_hidden_whole_and_said(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUETAKE_DENY_PATTERN", "ada")
    src = codex(tmp_path, [ev(1, "ITEM.STARTED:COMMAND_EXECUTION", input="ls -l"),
                           ev(2, "ITEM.COMPLETED:COMMAND_EXECUTION", output="drwx 2 ada ada 4096 x\nother row\n")])
    s = sc.build(src, cols=80, scanner=Scanner.from_env(), home="")
    texts = [l.text for l in s.lines if l.text]
    assert "[2 lines hidden: they contain a private name]" in texts
    assert not any("ada" in t for t in texts) and s.transforms["hidden"] == 1


def test_private_text_anywhere_else_stops_the_film(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUETAKE_DENY_PATTERN", "ada")
    src = codex(tmp_path, [ev(1, "ITEM.COMPLETED:AGENT_MESSAGE", "Thanks, Ada.")])
    with pytest.raises(sc.FilmError, match="privacy gate"):
        sc.build(src, cols=80, scanner=Scanner.from_env(), home="")


def test_reading_pace_and_the_clock(tmp_path):
    words = " ".join(["word"] * 30)
    src = codex(tmp_path, [ev(0, "ITEM.COMPLETED:AGENT_MESSAGE", words),
                           ev(40, "ITEM.STARTED:COMMAND_EXECUTION", input="make")])
    p = sc.Pacing()
    s = sc.build(src, cols=60, pacing=p, home="")
    msg = [l for l in s.lines if l.role == "message"]
    cmd = next(l for l in s.lines if l.role == "command")
    assert all(l.cps == p.message_cps for l in msg)
    gap = cmd.t0 - msg[-1].t1
    assert p.beat_min <= gap <= p.beat_max + p.idle_cap + 0.01  # a beat after the last word, never a long stall
    assert s.real_at(cmd.t0) == pytest.approx(40.0)  # the clock shows the command's real time
    assert s.rate_at(msg[0].t1) == 0  # the message had already arrived: the clock holds while it types
    assert s.rate_at(cmd.t0 - 0.25, h=0.2) > 10  # the agent's idle time passes fast, and the rate says so


def test_until_keeps_the_counts_to_what_the_film_shows(tmp_path):
    many = [ev(i, "ITEM.STARTED:COMMAND_EXECUTION", input=f"/bin/zsh -lc 'echo {i}'") for i in range(1, 40)]
    full = sc.build(codex(tmp_path, many), cols=80, home="")
    cut = sc.build(codex(tmp_path, many), cols=80, home="", until=2.0)
    assert cut.transforms["shell_wrap"] < full.transforms["shell_wrap"]
    assert all(l.t0 <= 2.0 + 1.5 for l in cut.lines)


BASE = """version: 1
output: demo.mp4
voice: {engine: none}
segments:
  - {id: t, kind: title, title: T}
"""


def cfg_with(tmp_path, film):
    p = tmp_path / "truetake.yaml"
    p.write_text(BASE + film, encoding="utf-8")
    return load(p)


@pytest.mark.parametrize("film, match", [
    ("film: {output: demo.mp4, pane: {source: codex}}", "never overwrites"),
    ("film: {output: f.mp4, colour: red}", "unknown key"),
    ("film: {output: f.mp4}", "is required"),
    ("film: {output: f.mp4, pane: {source: codex}, camera: [{at: 2, zoom: 5}]}", "between 1.0 and 2.0"),
    ("film: {output: f.mp4, pane: {source: codex}, camera: [{at: 5, zoom: 1.2}, {at: 2, zoom: 1.0}]}", "time order"),
    ("film: {output: f.mp4, pane: {source: codex}, camera: [{at: 5, zoom: 1.2, pane: nobody}]}", "no pane"),
    ("film: {output: f.mp4, pane: {source: codex}, pacing: {typing: 3}}", "unknown key"),
    ("film: {output: f.mp4, pane: {source: codex}, panes: [{source: codex}]}", "not both"),
    ("film: {output: f.mp4, panes: [{id: a, source: codex}, {id: a, source: codex}]}", "unique"),
])
def test_film_config_refuses(tmp_path, film, match):
    with pytest.raises(ConfigError, match=match):
        spec(cfg_with(tmp_path, film))


def test_film_preview_draws(tmp_path):
    pytest.importorskip("skia")
    from truetake.film import theme as thememod
    from truetake.film.render import FilmSpec, PaneSpec, render

    src = codex(tmp_path, [ev(0, "ITEM.COMPLETED:AGENT_MESSAGE", "Hello."),
                           ev(1, "ITEM.STARTED:COMMAND_EXECUTION", input="make")])
    s = sc.build(src, cols=40, home="")
    try:
        th = thememod.load("mocha", {}, "monospace")
    except Exception as e:  # no fontconfig on this machine
        pytest.skip(str(e))
    fs = FilmSpec(output=tmp_path / "f.mp4", width=640, height=360, fps=12, duration=3, cols=40, rows=10,
                  panes=[PaneSpec("main", "T", "s")], camera=[], utc_offset=7)
    out = render(fs, s, th, sc.Pacing(), tmp_path, preview=[0.5, 2.0], log=lambda *_: None)
    assert len(out["preview"]) == 2


def test_panes_share_one_clock_in_real_time_order(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir(), b.mkdir()
    orch = codex(a, [ev(0, "ITEM.COMPLETED:AGENT_MESSAGE", "Starting."),
                     ev(30, "ITEM.STARTED:MCP_TOOL_CALL", name="mtm_team/submit")])
    sub = codex(b, [ev(10, "ITEM.COMPLETED:AGENT_MESSAGE", "Fixed the bug.")])
    s = sc.build_set([("orch", orch), ("sub", sub)], cols=60, home="")
    first = {pid: next(l.t0 for l in ls if l.text) for pid, ls in s.panes.items()}
    call = next(l for l in s.panes["orch"] if l.role == "call")
    assert first["orch"] < first["sub"] < call.t0  # 0 s, 10 s, 30 s: one timeline for every pane
    assert s.real_at(first["sub"]) == pytest.approx(10.0)
    assert s.real_at(call.t0) == pytest.approx(30.0)


def test_panes_need_timestamps(tmp_path):
    untimed = sc.Source([sc.Item("message", "hi")], None, "take")
    with pytest.raises(sc.FilmError, match="timestamps"):
        sc.build_set([("a", untimed), ("b", untimed)], cols=60, home="")


def test_outside_the_beats_the_film_is_a_time_lapse(tmp_path):
    events = [ev(i, "ITEM.STARTED:COMMAND_EXECUTION", input=f"step {i}") for i in range(0, 50, 2)]
    src = codex(tmp_path, events)
    read = sc.build_set([("m", src)], cols=60, home="")
    lapse = sc.build_set([("m", src)], cols=60, home="", beats=[(0.0, 4.0)])
    assert lapse.end < read.end / 3  # most of it runs fast
    late = [l for l in lapse.lines if l.text.startswith("$ step 4")]
    assert all(l.cps == 0 for l in late if l.text != "$ step 4")  # outside the beat, lines land whole
    assert lapse.lapses and lapse.rate_at(lapse.lapses[0][0] + 0.3, h=0.2) > 5  # and the clock races


def test_wall_clock_beats(tmp_path):
    from truetake.film import beats as parse_beats
    import datetime as dt

    t0 = dt.datetime(2026, 10, 29, 5, 34, 12, tzinfo=dt.timezone.utc)  # 12:34:12 at UTC+7
    assert parse_beats([{"from": "12:35", "to": "12:36:12"}], t0, 7) == [(48.0, 120.0)]
    assert parse_beats([{"from": 5, "to": 9}], None, None) == [(5.0, 9.0)]
    with pytest.raises(ConfigError, match="utc_offset"):
        parse_beats([{"from": "12:35", "to": "12:36"}], t0, None)


def test_auto_camera_reads_close_and_lapses_wide(tmp_path):
    pytest.importorskip("skia")
    from truetake.film import theme as thememod
    from truetake.film.render import FilmSpec, PaneSpec, Stage

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    orch = codex(a, [ev(0, "ITEM.COMPLETED:AGENT_MESSAGE", "Starting the match: checking the checkout and the environment first."),
                     *[ev(5 + i, "ITEM.STARTED:COMMAND_EXECUTION", input=f"poll {i}") for i in range(20)]])
    sub = codex(b, [ev(30 + i * 0.2, "ITEM.STARTED:COMMAND_EXECUTION", input=f"fix {i}") for i in range(10)])
    s = sc.build_set([("orch", orch), ("sub", sub)], cols=60, home="", beats=[(0.0, 3.0), (29.0, 33.0)])
    try:
        th = thememod.load("mocha", {}, "monospace")
    except Exception as e:
        pytest.skip(str(e))
    fs = FilmSpec(output=tmp_path / "f.mp4", width=1280, height=720, fps=12, duration=s.end, cols=60, rows=12,
                  panes=[PaneSpec("orch"), PaneSpec("sub")], camera=[], utc_offset=None, camera_auto=True)
    Stage(fs, s, th)
    pushes = [c for c in fs.camera if c.zoom > 1.0]
    assert [c.pane for c in pushes] == ["orch", "sub"]  # each beat frames the pane that writes in it
    for push in pushes:  # and every push ends in a wide shot
        assert any(c.zoom == 1.0 and c.at > push.at for c in fs.camera)
