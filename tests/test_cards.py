import subprocess

import pytest

from truetake import cards

SRC = [f"line {i}" for i in range(1, 31)] + [""]  # a 30-line file ending with a newline


def test_excerpt_single_range():
    rows = cards.excerpt(SRC, [(3, 5)])
    assert rows == [(3, "line 3"), (4, "line 4"), (5, "line 5")]


def test_excerpt_adds_one_skip_row_between_gaps_only():
    rows = cards.excerpt(SRC, [(1, 2), (3, 4), (10, 11)])
    assert [n for n, _ in rows] == [1, 2, 3, 4, None, 10, 11]
    assert rows[4] == (None, cards.SKIP)


def test_excerpt_past_end_of_file():
    with pytest.raises(cards.VerbatimError):
        cards.excerpt(SRC, [(29, 31)])


def test_excerpt_row_limit():
    with pytest.raises(cards.VerbatimError):
        cards.excerpt(SRC, [(1, 25)])


def test_verify_accepts_the_excerpt():
    ranges = [(2, 4), (8, 9)]
    cards.verify(cards.excerpt(SRC, ranges), SRC, ranges)


def test_verify_refuses_an_edited_line():
    ranges = [(2, 4)]
    rows = cards.excerpt(SRC, ranges)
    rows[1] = (3, "line 3 // tidied")
    with pytest.raises(cards.VerbatimError, match="line 3"):
        cards.verify(rows, SRC, ranges)


def test_verify_refuses_an_added_line():
    ranges = [(2, 4)]
    rows = cards.excerpt(SRC, ranges)
    rows.insert(1, (None, "// explanatory comment"))
    with pytest.raises(cards.VerbatimError):
        cards.verify(rows, SRC, ranges)


def test_verify_refuses_a_skip_row_where_there_is_no_gap():
    ranges = [(2, 3), (4, 5)]
    rows = cards.excerpt(SRC, ranges)
    rows.insert(2, (None, cards.SKIP))
    with pytest.raises(cards.VerbatimError):
        cards.verify(rows, SRC, ranges)


def test_verify_refuses_reordered_lines():
    ranges = [(2, 4)]
    rows = cards.excerpt(SRC, ranges)
    rows[0], rows[1] = rows[1], rows[0]
    with pytest.raises(cards.VerbatimError):
        cards.verify(rows, SRC, ranges)


def test_git_show_reads_the_pinned_commit_not_the_working_tree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email="]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "a.py").write_text("x = 1\ny = 2\n")
    subprocess.run(git[:3] + ["add", "a.py"], check=True)
    subprocess.run(git + ["commit", "-qm", "one"], check=True)
    sha = subprocess.run(git[:3] + ["rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    (repo / "a.py").write_text("x = 100\ny = 2\n")  # uncommitted edit must not leak into the card
    lines = cards.git_show(repo, sha, "a.py")
    assert lines[:2] == ["x = 1", "y = 2"]
    with pytest.raises(cards.VerbatimError):
        cards.git_show(repo, sha, "missing.py")
