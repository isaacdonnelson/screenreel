"""Eased synthetic cursor. Positions are a function of output time."""

from __future__ import annotations

import math
from dataclasses import dataclass


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def minimum_jerk(u: float) -> float:
    t = clamp(u, 0.0, 1.0)
    return 10 * t ** 3 - 15 * t ** 4 + 6 * t ** 5


def travel_duration(distance_px: float) -> float:
    """Distance curve from CUR-4.

    The published clamp tops out at 0.95 s, which would push long moves past
    2400 px/s. The speed cap wins, so those moves run longer than 0.95 s.
    """
    distance = max(0.0, float(distance_px))
    nominal = clamp(0.30 + 0.00045 * distance, 0.35, 0.95)
    if distance <= 0:
        return nominal
    limited = 1.875 * distance / 2400.0
    return max(nominal, limited)


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def distance(self, other: Point) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)


@dataclass(frozen=True)
class Travel:
    start: Point
    end: Point
    t0: float
    duration: float
    arrive: float
    press: float
    release: float
    c1: Point
    c2: Point
    offset_fraction: float

    def position(self, t: float) -> Point:
        if t <= self.t0:
            return self.start
        if t >= self.arrive:
            return self.end
        span = max(self.duration, 1e-6)
        return cubic(self.start, self.c1, self.c2, self.end, minimum_jerk((t - self.t0) / span))

    def press_scale(self, t: float) -> float:
        if t < self.press:
            return 1.0
        if t < self.press + 0.080:
            return 1.0 + (0.86 - 1.0) * minimum_jerk((t - self.press) / 0.080)
        if t < self.release:
            return 0.86
        if t < self.release + 0.140:
            return 0.86 + (1.0 - 0.86) * minimum_jerk((t - self.release) / 0.140)
        return 1.0


def cubic(p0: Point, p1: Point, p2: Point, p3: Point, s: float) -> Point:
    u = clamp(s, 0.0, 1.0)
    v = 1.0 - u
    x = v ** 3 * p0.x + 3 * v * v * u * p1.x + 3 * v * u * u * p2.x + u ** 3 * p3.x
    y = v ** 3 * p0.y + 3 * v * v * u * p1.y + 3 * v * u * u * p2.y + u ** 3 * p3.y
    return Point(x, y)


def control_points(start: Point, end: Point, index: int) -> tuple[Point, Point, float]:
    dx = end.x - start.x
    dy = end.y - start.y
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return start, end, 0.0
    fraction = 0.06 + 0.08 * ((index % 5) / 4.0)
    side = -1.0 if index % 2 else 1.0
    px = -dy / dist
    py = dx / dist
    offset = dist * fraction
    c1 = Point(start.x + dx * 0.33 + px * offset * side, start.y + dy * 0.33 + py * offset * side)
    c2 = Point(
        start.x + dx * 0.66 + px * offset * side * 0.65,
        start.y + dy * 0.66 + py * offset * side * 0.65,
    )
    return c1, c2, fraction


def plan_travel(start: Point, end: Point, press: float, index: int, release: float | None = None) -> Travel:
    duration = travel_duration(start.distance(end))
    arrive = press - 0.12
    c1, c2, fraction = control_points(start, end, index)
    return Travel(
        start=start,
        end=end,
        t0=arrive - duration,
        duration=duration,
        arrive=arrive,
        press=press,
        release=press + 0.08 if release is None else release,
        c1=c1,
        c2=c2,
        offset_fraction=fraction,
    )


def sample_cursor(travels: list[Travel], t: float, idle: Point) -> tuple[Point, float]:
    if not travels:
        return idle, 1.0
    if t < travels[0].t0:
        return travels[0].start, 1.0
    for index, travel in enumerate(travels):
        nxt = travels[index + 1] if index + 1 < len(travels) else None
        end = nxt.t0 if nxt is not None else float("inf")
        if travel.t0 <= t < end:
            return travel.position(t), travel.press_scale(t)
    last = travels[-1]
    return last.position(t), last.press_scale(t)


def peak_speed(travel: Travel, samples: int = 240) -> float:
    if travel.duration <= 0:
        return 0.0
    fastest = 0.0
    previous = travel.position(travel.t0)
    for index in range(1, samples + 1):
        t = travel.t0 + travel.duration * index / samples
        point = travel.position(t)
        dt = travel.duration / samples
        fastest = max(fastest, previous.distance(point) / dt)
        previous = point
    return fastest
