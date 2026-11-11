"""The explainer stage and the photosensitivity check."""
import gc

import numpy as np
import pytest

from truetake.film.explain import Film, Scene, Theme, colormap, ease, reading_time, tween, window
from truetake.flash import flashes


def test_ease_and_tween_are_pure_and_bounded():
    assert ease(0.0, 1.0, 2.0) == 0.0 and ease(5.0, 1.0, 2.0) == 1.0
    assert 0 < ease(2.0, 1.0, 2.0, "inout") < 1
    assert tween(2.0, 0.0, 2.0, (0, 10), (10, 20)) == (10, 20)
    assert window(5, 1, 4) == 0.0 and window(2.5, 1, 4) == 1.0


def test_colormap_is_fixed_and_ranged():
    a = np.array([[0.0, 1.0]])
    rgb = colormap(a)
    assert rgb.shape == (1, 2, 3) and rgb.dtype == np.uint8
    assert tuple(rgb[0, 0]) != tuple(rgb[0, 1])
    assert np.array_equal(colormap(a), colormap(a))


def test_film_check_finds_rushed_and_overlapping_subtitles():
    s = Scene("a", 3.0, lambda ctx, t: None, subtitles=[(0.0, 0.5, "Far too many words to read in half a second"),
                                                     (0.4, 2.9, "Overlap")])
    problems = Film([s], Theme()).check()
    assert any("needs" in p for p in problems) and any("overlaps" in p for p in problems)
    ok = Scene("b", 4.0, lambda ctx, t: None, subtitles=[(0.0, reading_time("Short and fine."), "Short and fine.")])
    assert Film([ok], Theme()).check() == []


def test_image_cache_never_returns_a_freed_arrays_picture():
    """Ids are reused once an object is freed; the cache must not hand the old picture to the new array."""
    pytest.importorskip("skia")
    import skia

    from truetake.film.explain import Ctx

    surface = skia.Surface(64, 64)
    with surface as canvas:
        ctx = Ctx(canvas, Theme(), 64, 64, {})
        seen = set()
        for i in range(50):  # many short-lived arrays: ids get recycled
            a = np.full((4, 4, 3), i, np.uint8)
            ctx.image(a, 0, 0, 64, 64)
            entry = ctx._images[id(a)]
            assert entry[0] is a  # the cached picture belongs to this very array
            seen.add(id(a))
            del a
            gc.collect()


@pytest.mark.parametrize("signal, n", [
    ([0.1, 0.9] * 4, 6),            # 7 light/dark transitions; each one after the first completes a flash
    ([0.1, 0.12, 0.14, 0.16], 0),  # a slow drift is not a flash
    ([0.85, 0.95, 0.85, 0.95], 0),  # both states bright (darker side above 0.8): not a flash under WCAG
])
def test_flash_counting(signal, n):
    assert len(flashes(np.array(signal))) == n
