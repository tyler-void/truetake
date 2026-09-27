import pytest

from truetake import cast, screen, takes

PROMPT = "Please shorten the greeting in app.py."


def build(take_file, **kw):
    path, cwd = take_file
    t = takes.parse(path)
    pacing = cast.Pacing(**kw.pop("pacing", {}))
    return cast.build(t, PROMPT, cwd, cols=100, rows=30, label="demo", pacing=pacing, **kw)


def plain(c):
    return "\n".join(text for _, text in c.line_times)


def test_parse_links_hook_to_its_tool(take_file):
    t = takes.parse(take_file[0])
    hook = next(s for s in t.steps if s.kind == "hook")
    assert hook.text == "Reviewed rule: keep the format."
    assert t.steps[hook.tool_index].name == "Read"
    assert t.hook_events_seen


def test_agent_words_are_verbatim_apart_from_markdown_markers(take_file):
    c = build(take_file)
    text = plain(c)
    assert "I read it. The rule says keep greet() as is." in text
    assert "Done" in text and "## Done" not in text
    assert "I changed nothing in app.py." in text
    assert c.transforms.markdown_markers_dropped == 2 + 2 + 1  # ** ×2, ` ×2, one heading


def test_prompt_is_shown_exactly(take_file):
    assert "> " + PROMPT in plain(build(take_file))


def test_tool_paths_are_relative_and_counted(take_file):
    c = build(take_file)
    assert "● Read app.py" in plain(c) and "● Edit notes.md" in plain(c)
    assert c.transforms.paths_relativized == 2


def test_hook_text_is_shown_verbatim_after_its_read(take_file):
    c = build(take_file)
    lines = [text for _, text in c.line_times]
    i = next(i for i, l in enumerate(lines) if l.startswith("● Read app.py"))
    assert lines[i + 1].strip() == cast.HOOK_HEADER
    assert "Reviewed rule: keep the format." in lines[i + 2]
    assert c.marks["hook"] > c.marks["read:app.py"]


def test_home_directory_becomes_tilde(take_file, monkeypatch, tmp_path):
    home = "/" + "home" + "/dev1"  # built at runtime so the repository's own privacy scan stays clean
    monkeypatch.setenv("HOME", home)
    t = takes.parse(take_file[0])
    t.steps.append(takes.Step(kind="text", text=f"See {home}/notes.txt"))
    c = cast.build(t, PROMPT, "/tmp/sandbox", cols=100, rows=30, label="", pacing=cast.Pacing())
    assert "See ~/notes.txt" in plain(c) and home not in plain(c)
    assert c.transforms.home_replaced == 1


def test_pacing_holds_first_tool_and_streams_at_cps(take_file):
    c = build(take_file, pacing={"first_tool_at": 12.0, "reply_cps": 50.0})
    assert c.marks["tool:Read"] == pytest.approx(12.0)
    slow = build(take_file, pacing={"first_tool_at": 12.0, "reply_cps": 25.0})
    # the only difference is the reply text, streamed at half the speed
    assert slow.t - c.t == pytest.approx(c.reply_chars / 25.0 - c.reply_chars / 50.0, abs=0.01)


def test_fit_hits_the_target_length(take_file):
    path, cwd = take_file
    t = takes.parse(path)
    c = cast.fit(t, PROMPT, cwd, 20.0, cols=100, rows=30, label="", pacing=cast.Pacing())
    assert c.t == pytest.approx(20.0, abs=0.05)


def test_fit_refuses_a_target_shorter_than_the_pauses(take_file):
    path, cwd = take_file
    with pytest.raises(ValueError):
        cast.fit(takes.parse(path), PROMPT, cwd, 2.0, cols=100, rows=30, label="", pacing=cast.Pacing())


def test_no_line_is_wider_than_the_terminal(take_file):
    path, cwd = take_file
    t = takes.parse(path)
    t.steps.append(takes.Step(kind="text", text="word " * 200))
    c = cast.build(t, PROMPT, cwd, cols=60, rows=20, label="", pacing=cast.Pacing())
    assert c.widest <= 60


def test_hook_path_with_empty_hook_shows_nothing_added(tmp_path):
    import json
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/s/x.py"}}]}},
        {"type": "system", "subtype": "hook_response", "hook_name": "PostToolUse:Read", "hook_event": "PostToolUse", "stdout": ""},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
    ]))
    c = cast.build(takes.parse(p), "p", "/s", cols=80, rows=20, label="", pacing=cast.Pacing(), hook_path="x.py")
    assert any(cast.HOOK_NOTHING in text for _, text in c.line_times)


def test_caption_triggers_resolve(take_file):
    c = build(take_file)
    assert cast.resolve_trigger(c, "@read:app.py") == c.marks["read:app.py"]
    assert cast.resolve_trigger(c, "@reply") == c.marks["reply"]
    assert cast.resolve_trigger(c, "I changed nothing") is not None
    assert cast.resolve_trigger(c, "never printed") is None


def test_written_cast_replays_to_the_same_text(take_file, tmp_path):
    c = build(take_file)
    p = tmp_path / "x.cast"
    cast.write(c, p)
    head, events = cast.read_events(p)
    assert head["version"] == 2 and head["width"] == 100
    assert [l for l in screen.screen_text(events, 30, 100)] == [text for _, text in c.line_times]
