import re

import pytest

from truetake.scan import Scanner

# Fixtures are assembled at runtime so that this repository's own privacy scan finds nothing in it.
HOME = "/" + "home" + "/dev1"
MAC_HOME = "/" + "Users" + "/dev1"
EMAIL = "someone" + "@" + "example.org"


def scanner(**kw):
    base = dict(deny=re.compile(r"\bAlex(?:andra)?\s+Example\b", re.I), home=HOME, username="dev1", allow_emails=())
    base.update(kw)
    return Scanner(**base)


def kinds(hits):
    return sorted({h.kind for h in hits})


def test_clean_text_passes():
    assert scanner().scan("t", "The agent read app.py and changed nothing.") == []


def test_deny_pattern_hits_and_is_redacted():
    hits = scanner().scan("narration", "Recorded by alexandra example last week.")
    assert kinds(hits) == ["deny"]
    assert "alexandra" not in hits[0].snippet.lower() and "[deny]" in hits[0].snippet


def test_home_directory_literal_and_generic_forms():
    s = scanner(username="")
    assert kinds(s.scan("t", f"open {HOME}/x.py")) == ["home"]
    assert kinds(s.scan("t", f"open {MAC_HOME}/x.py")) == ["home"]
    assert kinds(s.scan("t", "C:" + "\\" + "Users" + "\\" + "dev1" + "\\x.py")) == ["home"]
    assert s.scan("t", "src/Users/list.ts and /tmp/sandbox") == []


def test_username_as_a_whole_word_only():
    s = scanner()
    assert kinds(s.scan("t", "signed off by dev1")) == ["username"]
    assert s.scan("t", "dev10 and adev1x are other words") == []


def test_email_and_allowlist():
    assert kinds(scanner().scan("t", f"mail {EMAIL}")) == ["email"]
    assert scanner(allow_emails=("*@example.org",)).scan("t", f"mail {EMAIL}") == []


def test_hits_report_source_and_line():
    hits = scanner().scan("scene/terminal", f"ok\nok\nmail {EMAIL}")
    assert hits[0].source == "scene/terminal" and hits[0].line == 3


def test_from_env_reads_the_pattern(monkeypatch):
    monkeypatch.setenv("TRUETAKE_DENY_PATTERN", r"secret\s+client")
    s = Scanner.from_env()
    assert kinds(s.scan("t", "for Secret Client only")) == ["deny"]
    assert s.describe()["deny_pattern_set"] is True


def test_from_env_without_a_pattern(monkeypatch):
    monkeypatch.delenv("TRUETAKE_DENY_PATTERN", raising=False)
    assert Scanner.from_env().deny is None


def test_bad_pattern_is_an_error(monkeypatch):
    monkeypatch.setenv("TRUETAKE_DENY_PATTERN", "(unclosed")
    with pytest.raises(ValueError):
        Scanner.from_env()
