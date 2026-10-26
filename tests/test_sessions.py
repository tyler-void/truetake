"""Imported takes, resumed sessions and stream annotations: follow-up prompts, tool-result sizes, PreToolUse
rewrites from the transcript, final usage from the transcript, the usage bar, cwd display and pairs."""
import json

import pytest

from truetake import cast, config, importer, state, takes
from truetake.config import ConfigError

CWD = "/tmp/run-7/repo"


def line(e):
    return json.dumps(e) + "\n"


def usage(read, write, out):
    return {"input_tokens": 2, "cache_read_input_tokens": read, "cache_creation_input_tokens": write,
            "cache_creation": {"ephemeral_5m_input_tokens": write, "ephemeral_1h_input_tokens": 0}, "output_tokens": out}


@pytest.fixture
def session(tmp_path):
    """Two `claude -p` invocations of one session (prompt 1, then --resume), plus its transcript."""
    init = {"type": "system", "subtype": "init", "cwd": CWD, "model": "m", "claude_code_version": "9.9.9"}
    s1 = tmp_path / "stream_p1.jsonl"
    s1.write_text(
        line(init)
        # the stream's usage is provisional: output_tokens counted before the message ends
        + line({"type": "assistant", "message": {"id": "msg_1", "usage": usage(1000, 500, 3), "content": [
            {"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {"command": f"cd {CWD}; grep -rn Foo ."}}]}})
        + line({"type": "system", "subtype": "hook_response", "hook_name": "PreToolUse:Bash", "hook_event": "PreToolUse",
                "stdout": "", "output": ""})
        + line({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "x" * 1234}]}})
        + line({"type": "assistant", "message": {"id": "msg_2", "usage": usage(1500, 200, 4), "content": [
            {"type": "text", "text": "Found it."}]}})
        + line({"type": "result", "subtype": "success", "duration_ms": 2000}), encoding="utf-8")
    s2 = tmp_path / "stream_p2.jsonl"
    s2.write_text(
        line(init)
        + line({"type": "assistant", "message": {"id": "msg_3", "usage": usage(1700, 100, 2), "content": [
            {"type": "text", "text": "Summary."}]}})
        + line({"type": "result", "subtype": "success", "duration_ms": 1000}), encoding="utf-8")
    tr = tmp_path / "transcript.jsonl"
    rewrite = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecisionReason": "auto-rewrite",
                                                 "updatedInput": {"command": f"cd {CWD}; rtk grep -rn Foo ."}}})
    tr.write_text(
        line({"type": "assistant", "message": {"id": "msg_1", "usage": usage(1000, 500, 120)}})
        + line({"type": "attachment", "attachment": {"type": "hook_success", "hookEvent": "PreToolUse", "hookName": "PreToolUse:Bash",
                                                     "toolUseID": "tu_1", "stdout": rewrite, "command": "rtk hook claude"}})
        + line({"type": "assistant", "message": {"id": "msg_2", "usage": usage(1500, 200, 80)}})
        + line({"type": "assistant", "message": {"id": "msg_3", "usage": usage(1700, 100, 40)}}), encoding="utf-8")
    return s1, s2, tr


def write_cfg(tmp_path, scenes_extra="", pair=True):
    text = f"""version: 1
work_dir: work
voice: {{engine: none}}
usage_prices: {{label: list price, per_mtok: {{input: 2, output: 10, cache_write_5m: 2.5, cache_write_1h: 4, cache_read: 0.1}}}}
segments:
  - id: a
    kind: scene
    cwd: {CWD}
    prompt: Find Foo.
    followups: [Summarize.]
    display_cwd: repo/
    annotate: {{sizes: true, rewrites: true, usage_bar: true}}
    prompt_seconds: 2
{scenes_extra}
"""
    if pair:
        text += """  - id: b
    kind: scene
    cwd: /tmp/other
    prompt: Find Foo.
  - id: both
    kind: pair
    left: a
    right: b
"""
    p = tmp_path / "truetake.yaml"
    p.write_text(text, encoding="utf-8")
    return config.load(p)


def test_import_keeps_provenance_and_transcript_facts(tmp_path, session):
    s1, s2, tr = session
    cfg = write_cfg(tmp_path)
    n = importer.import_take(cfg, cfg.segment("a"), [s1, s2], "run-7", tr)
    meta = json.loads((state.take_path(cfg, "a", n, ".meta.json")).read_text())
    assert meta["imported"]["source"] == "run-7" and meta["imported"]["invocations"] == 2
    assert meta["prompts"] == ["Find Foo.", "Summarize."]
    assert len(meta["imported"]["streams"][0]["sha256"]) == 64
    assert meta["imported"]["transcript"]["pre_tool_use_rewrites"] == 1


def test_import_refuses_a_prompt_count_mismatch(tmp_path, session):
    s1, _, tr = session
    cfg = write_cfg(tmp_path)
    with pytest.raises(ConfigError, match="invocation"):
        importer.import_take(cfg, cfg.segment("a"), [s1], "run-7", tr)


def test_parse_resumed_session_with_sidecar(tmp_path, session):
    s1, s2, tr = session
    cfg = write_cfg(tmp_path)
    n = importer.import_take(cfg, cfg.segment("a"), [s1, s2], "run-7", tr)
    t = takes.parse(state.take_path(cfg, "a", n, ".jsonl"), prompts=["Find Foo.", "Summarize."])
    kinds = [s.kind for s in t.steps]
    assert kinds == ["usage", "tool", "usage", "text", "prompt", "usage", "text"]
    tool = t.tools[0]
    assert tool.result_chars == 1234
    assert tool.rewrite.endswith("rtk grep -rn Foo .")
    assert [s.usage["output_tokens"] for s in t.requests] == [120, 80, 40]  # final numbers, not the stream's 3/4/2
    assert all(s.usage["final"] for s in t.requests)
    assert next(s for s in t.steps if s.kind == "prompt").text == "Summarize."


def test_without_transcript_usage_is_the_streams_and_not_final(session):
    s1, _, _ = session
    t = takes.parse(s1)
    assert [s.usage["output_tokens"] for s in t.requests] == [3, 4]
    assert not any(s.usage["final"] for s in t.requests)


def test_annotations_bar_and_cwd_display(tmp_path, session):
    s1, s2, tr = session
    cfg = write_cfg(tmp_path)
    n = importer.import_take(cfg, cfg.segment("a"), [s1, s2], "run-7", tr)
    t = takes.parse(state.take_path(cfg, "a", n, ".jsonl"), prompts=["Find Foo.", "Summarize."])
    prices = cfg.prices
    an = cast.Annotate(sizes=True, rewrites=True, usage_bar=True, prices=prices)
    c = cast.build(t, "Find Foo.", CWD, cols=100, rows=30, label="x", pacing=cast.Pacing(prompt_max_seconds=0.5),
                   annotate=an, display_cwd="repo/")
    text = "\n".join(x for _, x in c.line_times)
    assert "cd repo; grep -rn Foo ." in text and CWD not in text
    assert "PreToolUse hook ran instead: cd repo; rtk grep -rn Foo ." in text
    assert "the model received 1,234 characters" in text
    assert "> Summarize." in text
    assert c.transforms.cwd_display == "repo/" and c.transforms.cwd_in_commands == 2
    # the bar: one value per request, running cost from the final usage
    assert [x for _, x in c.status][0].startswith("1 request · context 1,502 tokens · $")
    total = sum(cast.request_cost(s.usage, prices) for s in t.requests)
    assert c.status[-1][1].endswith(f"${total:.3f}")
    assert [t0 for t0, _ in c.status] == sorted(t0 for t0, _ in c.status)


def test_prompt_seconds_caps_typing_time(session):
    s1, _, _ = session
    t = takes.parse(s1)
    long_prompt = "word " * 200
    slow = cast.build(t, long_prompt, CWD, cols=100, rows=30, label="", pacing=cast.Pacing())
    fast = cast.build(t, long_prompt, CWD, cols=100, rows=30, label="", pacing=cast.Pacing(prompt_max_seconds=2))
    assert slow.t - fast.t > 20


def test_pair_config_marks_its_scenes(tmp_path):
    cfg = write_cfg(tmp_path)
    assert cfg.segment("a").in_pair == "both" and cfg.segment("b").in_pair == "both"
    assert cfg.segment("both").cols == 92


def test_pair_must_name_two_scenes(tmp_path):
    p = tmp_path / "truetake.yaml"
    p.write_text("""version: 1
voice: {engine: none}
segments:
  - {id: t, kind: title, title: T}
  - {id: s, kind: scene, cwd: /tmp/x, prompt: P}
  - {id: p, kind: pair, left: s, right: t}
""", encoding="utf-8")
    with pytest.raises(ConfigError, match="not a scene"):
        config.load(p)


def test_usage_annotations_need_prices(tmp_path):
    p = tmp_path / "truetake.yaml"
    p.write_text("""version: 1
voice: {engine: none}
segments:
  - {id: s, kind: scene, cwd: /tmp/x, prompt: P, annotate: {usage_bar: true}}
""", encoding="utf-8")
    with pytest.raises(ConfigError, match="usage_prices"):
        config.load(p)


def test_long_tokens_wrap_instead_of_overflowing():
    lines = cast.wrap_block("x" * 250, 80)
    assert all(len(l) <= 80 for l in lines) and "".join(lines) == "x" * 250
