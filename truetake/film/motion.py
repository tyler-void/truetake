"""Springs whose value is a pure function of time.

A critically damped spring that is told "go to x at time t" responds with a known curve. A spring that is
told many such things responds with the sum of those curves (the system is linear), so its position at any
film time can be computed directly, for any frame, in any order, with no simulation state. Moves that should
track a steady motion (a cursor following typed text) are ramps; the spring's response to a ramp has a closed
form too.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


def step_response(u: float, w: float) -> float:
    """Unit step response of a critically damped spring with angular frequency `w`, `u` seconds after the step."""
    if u <= 0:
        return 0.0
    return 1.0 - (1.0 + w * u) * math.exp(-w * u)


def ramp_response(u: float, w: float) -> float:
    """Response to a ramp of slope 1 starting at u = 0: it follows the ramp with a lag of 2/w."""
    if u <= 0:
        return 0.0
    return u - 2.0 / w + (2.0 / w + u) * math.exp(-w * u)


@dataclass
class Spring:
    w: float
    x0: float = 0.0
    steps: list[tuple[float, float]] = field(default_factory=list)  # (time, change)
    ramps: list[tuple[float, float, float]] = field(default_factory=list)  # (start, end, slope)
    target: float | None = None

    def _last(self) -> float:
        return self.x0 if self.target is None else self.target

    def to(self, t: float, target: float) -> None:
        """At time `t`, start moving to `target`."""
        if target != self._last():
            self.steps.append((t, target - self._last()))
            self.target = target

    def glide(self, t0: float, t1: float, target: float) -> None:
        """Move steadily to `target` over [t0, t1]; the spring follows with a small, exact lag."""
        if t1 <= t0:
            return self.to(t0, target)
        if target != self._last():
            self.ramps.append((t0, t1, (target - self._last()) / (t1 - t0)))
            self.target = target

    def at(self, t: float) -> float:
        x = self.x0
        for t0, d in self.steps:
            if t0 <= t:
                x += d * step_response(t - t0, self.w)
        for t0, t1, k in self.ramps:
            if t0 <= t:
                x += k * (ramp_response(t - t0, self.w) - ramp_response(t - t1, self.w))
        return x

    def keyframes(self) -> list[dict]:
        """What the report lists: every move this spring was told to make."""
        return ([{"t": round(t, 4), "change": round(d, 4)} for t, d in self.steps]
                + [{"from": round(a, 4), "to": round(b, 4), "slope": round(k, 4)} for a, b, k in self.ramps])
