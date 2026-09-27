from truetake.screen import first_hit

ROWS, COLS = 10, 20


def cell(row, col):
    """A 10x20 px cell grid starting at the origin."""
    return col * 10.0, row * 20.0, col * 10.0 + 10, row * 20.0 + 20


def lines(*items):
    """items: (time, text) pairs, each printed as one line."""
    return [[t, "o", text + "\r\n"] for t, text in items]


CARD = (0, 120, 200, 200)  # rows 6-9, all columns


def test_text_drawn_into_the_rectangle():
    ev = lines(*[(float(i), f"line {i}") for i in range(8)])
    hit = first_hit(ev, ROWS, COLS, CARD, cell)
    assert hit.row == 6 and hit.t == 6.0 and hit.cause == "draw"


def test_text_above_the_rectangle_never_hits():
    ev = lines(*[(float(i), f"line {i}") for i in range(6)])
    assert first_hit(ev, ROWS, COLS, CARD, cell) is None


def test_blank_lines_do_not_count():
    ev = lines(*[(float(i), "   ") for i in range(9)])
    assert first_hit(ev, ROWS, COLS, CARD, cell) is None


def test_text_scrolled_up_into_the_rectangle():
    # A card high on the screen (rows 2-3); text only ever drawn on the bottom row after blank lines,
    # then the screen scrolls it up through the card.
    card = (0, 40, 200, 80)
    ev = lines(*[(0.0, "")] * 9) + [[1.0, "o", "bottom"]] + [[2.0 + i, "o", "\r\n"] for i in range(8)]
    hit = first_hit(ev, ROWS, COLS, card, cell)
    assert hit is not None and hit.cause == "scroll" and hit.row == 3
    assert hit.t == 2.0 + 5  # the 6th scroll moves "bottom" from row 9 up to row 3


def test_background_coloured_spaces_count_as_text():
    ev = [[0.0, "o", "\r\n" * 7], [3.0, "o", "\x1b[48;2;49;50;68m      \x1b[0m\r\n"]]
    hit = first_hit(ev, ROWS, COLS, CARD, cell)
    assert hit is not None and hit.t == 3.0


def test_truecolour_foreground_with_48_is_not_a_background():
    ev = [[0.0, "o", "\r\n" * 7], [3.0, "o", "\x1b[38;2;48;48;48m      \x1b[0m\r\n"]]
    assert first_hit(ev, ROWS, COLS, CARD, cell) is None


def test_long_line_auto_wraps_into_the_rectangle():
    ev = [[0.0, "o", "\r\n" * 5], [4.0, "o", "x" * 25]]  # 20 columns on row 5, then wraps to row 6
    hit = first_hit(ev, ROWS, COLS, CARD, cell)
    assert hit.row == 6 and hit.t == 4.0
