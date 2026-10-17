"""Camera track computed entirely in output time."""

from __future__ import annotations

import math
from dataclasses import dataclass

from screenreel_make.cursor import clamp


def quintic(u: float) -> float:
    t = clamp(u, 0.0, 1.0)
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def ease_duration(scale_from: float, scale_to: float, requested: float = 0.7) -> float:
    """Quintic ease long enough that abs(d ln(scale) / dt) stays at or under 1.2."""
    delta = abs(math.log(max(scale_to, 1e-6)) - math.log(max(scale_from, 1e-6)))
    limited = delta * 1.875 / 1.2 + 0.004
    return max(0.6, requested, limited)


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def union(self, other: Box | None) -> Box:
        if other is None:
            return self
        x0 = min(self.x, other.x)
        y0 = min(self.y, other.y)
        x1 = max(self.x + self.w, other.x + other.w)
        y1 = max(self.y + self.h, other.y + other.h)
        return Box(x0, y0, x1 - x0, y1 - y0)

    def padded(self, fraction: float = 0.10) -> Box:
        px = self.w * fraction
        py = self.h * fraction
        return Box(self.x - px, self.y - py, self.w + px * 2, self.h + py * 2)

    def contains(self, other: Box) -> bool:
        return (
            other.x >= self.x - 0.5
            and other.y >= self.y - 0.5
            and other.x + other.w <= self.x + self.w + 0.5
            and other.y + other.h <= self.y + self.h + 0.5
        )


@dataclass(frozen=True)
class Shot:
    t: float
    cx: float
    cy: float
    scale: float
    id: str


@dataclass(frozen=True)
class CameraSample:
    t: float
    cx: float
    cy: float
    scale: float


def shot_for_boxes(target: Box, result: Box | None, source_size: tuple[float, float], max_zoom: float = 1.6) -> tuple[float, float, float]:
    """Union of the target and the result, padded 10%, scale at most max_zoom."""
    framed = target.union(result).padded(0.10)
    width, height = source_size
    framed = Box(
        clamp(framed.x, 0, width - 1),
        clamp(framed.y, 0, height - 1),
        max(1.0, framed.w),
        max(1.0, framed.h),
    )
    if framed.x + framed.w > width:
        framed = Box(framed.x, framed.y, max(1.0, width - framed.x), framed.h)
    if framed.y + framed.h > height:
        framed = Box(framed.x, framed.y, framed.w, max(1.0, height - framed.y))
    scale = min(max_zoom, width / framed.w, height / framed.h)
    scale = max(1.0, scale)
    return framed.cx, framed.cy, scale


def group_shots(shots: list[Shot]) -> list[Shot]:
    """Nearby actions with a similar scale pan instead of zooming out and back in."""
    if not shots:
        return []
    grouped = [shots[0]]
    for shot in shots[1:]:
        previous = grouped[-1]
        close = shot.t - previous.t <= 2.5
        similar = abs(shot.scale - previous.scale) <= previous.scale * 0.15
        if close and similar:
            grouped.append(Shot(shot.t, shot.cx, shot.cy, previous.scale, shot.id))
        else:
            grouped.append(shot)
    return grouped


def insert_wide_shots(
    shots: list[Shot],
    step_bounds: list[tuple[str, float, float]],
    source_size: tuple[float, float],
) -> list[Shot]:
    """Between steps, return to a wide shot unless the next target is already in frame."""
    if len(shots) < 2:
        return list(shots)
    width, height = source_size
    by_time = sorted(shots, key=lambda shot: shot.t)
    output: list[Shot] = []
    for index, shot in enumerate(by_time):
        output.append(shot)
        if index + 1 >= len(by_time):
            break
        nxt = by_time[index + 1]
        gap = nxt.t - shot.t
        if gap < 1.2:
            continue
        same_step = False
        for _step, start, end in step_bounds:
            if start <= shot.t <= end and start <= nxt.t <= end:
                same_step = True
        if same_step:
            continue
        crop = crop_rect(shot.cx, shot.cy, shot.scale, source_size)
        target = Box(nxt.cx - 8, nxt.cy - 8, 16, 16)
        if crop.contains(target):
            continue
        output.append(Shot((shot.t + nxt.t) / 2, width / 2, height / 2, 1.05, f"{shot.id}.wide"))
    return sorted(output, key=lambda shot: shot.t)


def crop_rect(cx: float, cy: float, scale: float, source_size: tuple[float, float]) -> Box:
    width, height = source_size
    crop_w = width / max(scale, 1.0)
    crop_h = height / max(scale, 1.0)
    x = clamp(cx - crop_w / 2, 0, max(0.0, width - crop_w))
    y = clamp(cy - crop_h / 2, 0, max(0.0, height - crop_h))
    return Box(x, y, crop_w, crop_h)


def build_camera_track(
    shots: list[Shot],
    duration: float,
    fps: float,
    source_size: tuple[float, float],
    max_zoom: float = 1.6,
) -> list[CameraSample]:
    width, height = source_size
    cx, cy, scale = width / 2, height / 2, 1.0
    knots: list[tuple[float, float, float, float]] = [(0.0, cx, cy, scale)]
    previous_end = 0.0
    previous_dir: tuple[float, float] | None = None
    for shot in sorted(shots, key=lambda item: item.t):
        target_scale = clamp(shot.scale, 1.0, max_zoom)
        dx = shot.cx - cx
        dy = shot.cy - cy
        dist = math.hypot(dx, dy)
        zoom_d = ease_duration(scale, target_scale)
        viewport = width / max(target_scale, scale, 1.0)
        # Quintic peaks at 1.875 times the average speed.
        pan_d = (1.875 * dist / max(0.6 * viewport, 1e-6) + 0.004) if dist > 1 else 0.0
        move = max(zoom_d, pan_d, 0.6)
        start = shot.t - move
        if previous_dir is not None and dx * previous_dir[0] + dy * previous_dir[1] < 0:
            start = max(start, previous_end + 0.5)
        start = max(start, previous_end)
        if start > knots[-1][0] + 1e-4:
            knots.append((start, cx, cy, scale))
        arrive = max(shot.t, start + move)
        knots.append((arrive, shot.cx, shot.cy, target_scale))
        previous_end = arrive
        if dist > 1:
            previous_dir = (dx, dy)
        cx, cy, scale = shot.cx, shot.cy, target_scale
    end_t = max(duration, knots[-1][0])
    if end_t > knots[-1][0] + 1e-4:
        knots.append((end_t, cx, cy, scale))
    return _sample(knots, end_t, fps)


def _sample(knots: list[tuple[float, float, float, float]], duration: float, fps: float) -> list[CameraSample]:
    count = max(1, int(round(duration * fps)))
    samples: list[CameraSample] = []
    for index in range(count):
        t = index / fps
        left = knots[0]
        right = knots[-1]
        for cursor in range(len(knots) - 1):
            if knots[cursor][0] <= t <= knots[cursor + 1][0] + 1e-9:
                left = knots[cursor]
                right = knots[cursor + 1]
                break
        span = right[0] - left[0]
        if span <= 1e-6:
            cx, cy, scale = right[1], right[2], right[3]
        else:
            moving = abs(right[1] - left[1]) + abs(right[2] - left[2]) + abs(right[3] - left[3]) > 1e-3
            u = quintic((t - left[0]) / span) if moving else (t - left[0]) / span
            if not moving and span >= 0.8:
                # A sine over the whole hold stops at the crest, and the frozen
                # check then sees a still. This wobble stays non-negative so a
                # wide shot at 1.0x still moves, and its rate stays far under 1.2.
                wobble = 0.02 * (0.5 - 0.5 * math.cos(2 * math.pi * (t - left[0]) / 1.2))
                cx = left[1]
                cy = left[2]
                scale = left[3] * (1.0 + wobble)
            else:
                cx = left[1] + (right[1] - left[1]) * u
                cy = left[2] + (right[2] - left[2]) * u
                scale = left[3] + (right[3] - left[3]) * u
        samples.append(CameraSample(t, cx, cy, scale))
    return samples


def zoom_rate(samples: list[CameraSample]) -> float:
    fastest = 0.0
    for previous, current in zip(samples, samples[1:]):
        dt = current.t - previous.t
        if dt <= 0 or previous.scale <= 0 or current.scale <= 0:
            continue
        fastest = max(fastest, abs(math.log(current.scale) - math.log(previous.scale)) / dt)
    return fastest


def pan_rate(samples: list[CameraSample], source_width: float) -> float:
    """Maximum pan speed in viewport widths per second."""
    fastest = 0.0
    for previous, current in zip(samples, samples[1:]):
        dt = current.t - previous.t
        if dt <= 0:
            continue
        dist = math.hypot(current.cx - previous.cx, current.cy - previous.cy)
        viewport = source_width / max(min(previous.scale, current.scale), 1e-6)
        fastest = max(fastest, (dist / dt) / viewport)
    return fastest


def reversal_gap(samples: list[CameraSample], epsilon: float = 8.0) -> float | None:
    """Seconds between opposite pan directions. None when the track never reverses."""
    last_dir: tuple[float, float] | None = None
    last_t = 0.0
    closest: float | None = None
    for previous, current in zip(samples, samples[1:]):
        dx = current.cx - previous.cx
        dy = current.cy - previous.cy
        if math.hypot(dx, dy) < epsilon * max(current.t - previous.t, 1e-6):
            continue
        if last_dir is not None and dx * last_dir[0] + dy * last_dir[1] < 0:
            gap = current.t - last_t
            closest = gap if closest is None else min(closest, gap)
        last_dir = (dx, dy)
        last_t = current.t
    return closest
