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
    ("film: {output: f.mp4, camera: [{at: 2, zoom: 5}]}", "between 1.0 and 2.0"),
    ("film: {output: f.mp4, camera: [{at: 5, zoom: 1.2}, {at: 2, zoom: 1.0}]}", "time order"),
    ("film: {output: f.mp4, pacing: {typing: 3}}", "unknown key"),
])
def test_film_config_refuses(tmp_path, film, match):
    with pytest.raises(ConfigError, match=match):
        spec(cfg_with(tmp_path, film))


def test_film_preview_draws(tmp_path):
    pytest.importorskip("skia")
    from truetake.film import theme as thememod
    from truetake.film.render import FilmSpec, render

    src = codex(tmp_path, [ev(0, "ITEM.COMPLETED:AGENT_MESSAGE", "Hello."),
                           ev(1, "ITEM.STARTED:COMMAND_EXECUTION", input="make")])
    s = sc.build(src, cols=40, home="")
    try:
        th = thememod.load("mocha", {}, "monospace")
    except Exception as e:  # no fontconfig on this machine
        pytest.skip(str(e))
    fs = FilmSpec(output=tmp_path / "f.mp4", width=640, height=360, fps=12, duration=3, cols=40, rows=10,
                  title="T", subtitle="s", camera=[], utc_offset=7)
    out = render(fs, s, th, sc.Pacing(), tmp_path, preview=[0.5, 2.0], log=lambda *_: None)
    assert len(out["preview"]) == 2
