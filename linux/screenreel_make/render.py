"""Output-time compositor.

The camera, cursor, and ripple are sampled on the output clock. Speed ramps
only choose which source frame is shown; they do not shorten an ease.
"""

from __future__ import annotations

import math
import os
import subprocess
from typing import Callable

import cv2
import numpy as np

from screenreel_make.blur import apply_blurs
from screenreel_make.camera import CameraSample, crop_rect
from screenreel_make.cursor import Point, Travel, sample_cursor

FrameSource = Callable[[float], np.ndarray]


def parse_hex(value: str) -> tuple[int, int, int]:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        return (30, 27, 75)
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def output_size(preview: bool, canvas: tuple[int, int] | None = None) -> tuple[int, int, int]:
    if preview:
        return 960, 540, 30
    if canvas:
        return canvas[0], canvas[1], 60
    return 1920, 1080, 60


def map_point(point: Point, sample: CameraSample, source_size: tuple[float, float], dest: tuple[float, float, float, float]) -> Point:
    crop = crop_rect(sample.cx, sample.cy, sample.scale, source_size)
    dx, dy, dw, dh = dest
    u = (point.x - crop.x) / max(crop.w, 0.001)
    v = (point.y - crop.y) / max(crop.h, 0.001)
    return Point(dx + u * dw, dy + v * dh)


def _window(canvas_w: int, canvas_h: int, padding: float) -> tuple[float, float, float, float]:
    pad = max(0.0, padding)
    width = max(2.0, canvas_w - pad * 2)
    height = max(2.0, canvas_h - pad * 2)
    # At least 82% of the canvas width.
    if width < canvas_w * 0.82:
        width = canvas_w * 0.82
        pad = (canvas_w - width) / 2
        height = max(2.0, canvas_h - pad * 2)
    return pad, pad, width, height


def _rounded_mask(width: int, height: int, radius: int) -> np.ndarray:
    mask = np.zeros((height, width), np.uint8)
    cv2.rectangle(mask, (radius, 0), (width - radius, height), 255, -1)
    cv2.rectangle(mask, (0, radius), (width, height - radius), 255, -1)
    cv2.circle(mask, (radius, radius), radius, 255, -1)
    cv2.circle(mask, (width - radius, radius), radius, 255, -1)
    cv2.circle(mask, (radius, height - radius), radius, 255, -1)
    cv2.circle(mask, (width - radius, height - radius), radius, 255, -1)
    return mask


def _cursor_sprite(height_px: int = 44) -> tuple[np.ndarray, np.ndarray]:
    """Arrow rendered at 4x and downsampled. Hotspot is the tip, pixel (0, 0) of the sprite."""
    scale = 4
    big_h = height_px * scale
    # Tip at the top-left of the glyph, matching a standard pointer.
    points = np.array([
        [0, 0],
        [0, 19],
        [4.7, 14.5],
        [7.8, 21.4],
        [10.6, 20.2],
        [7.4, 13.4],
        [13.4, 13.2],
    ], np.float32)
    glyph_h = 22
    fitted = points * (big_h / glyph_h) * 0.92
    canvas = np.zeros((big_h + 24, big_h + 24, 4), np.uint8)
    shifted = fitted + np.array([8, 8], np.float32)
    contour = np.round(shifted).astype(np.int32)
    cv2.fillPoly(canvas, [contour], (15, 23, 42, 255))
    cv2.polylines(canvas, [contour], True, (255, 255, 255, 255), max(2, scale))
    shadow = np.zeros_like(canvas)
    cv2.fillPoly(shadow, [contour], (0, 0, 0, int(255 * 0.35)))
    shadow = cv2.GaussianBlur(shadow, (0, 0), scale * 1.5)
    shadow_canvas = np.zeros_like(canvas)
    ox, oy = 2 * scale, 3 * scale
    shadow_canvas[oy:, ox:] = shadow[: shadow_canvas.shape[0] - oy, : shadow_canvas.shape[1] - ox]
    out = _over(shadow_canvas, canvas)
    small = cv2.resize(out, (height_px + 6, height_px + 6), interpolation=cv2.INTER_AREA)
    # Tip landed at (8, 8) in the 4x buffer, which is (2, 2) after the resize ratio.
    tip_x = int(round(8 * small.shape[1] / canvas.shape[1]))
    tip_y = int(round(8 * small.shape[0] / canvas.shape[0]))
    sprite = small
    hotspot = (tip_x, tip_y)
    alpha = sprite[:, :, 3]
    return sprite, np.array(hotspot)


def _over(base: np.ndarray, top: np.ndarray) -> np.ndarray:
    if base.shape[2] == 3:
        base = cv2.cvtColor(base, cv2.COLOR_BGR2BGRA)
    alpha = top[:, :, 3:4].astype(np.float32) / 255.0
    mixed = base.astype(np.float32) * (1 - alpha) + top.astype(np.float32) * alpha
    return mixed.astype(np.uint8)


def _blit(frame: np.ndarray, sprite: np.ndarray, origin_x: int, origin_y: int, opacity: float = 1.0) -> None:
    if opacity <= 0.01:
        return
    height, width = frame.shape[:2]
    sh, sw = sprite.shape[:2]
    x0 = max(0, origin_x)
    y0 = max(0, origin_y)
    x1 = min(width, origin_x + sw)
    y1 = min(height, origin_y + sh)
    if x1 <= x0 or y1 <= y0:
        return
    sx0 = x0 - origin_x
    sy0 = y0 - origin_y
    patch = sprite[sy0: sy0 + (y1 - y0), sx0: sx0 + (x1 - x0)]
    region = frame[y0:y1, x0:x1]
    alpha = patch[:, :, 3:4].astype(np.float32) * (opacity / 255.0)
    region[:] = (region.astype(np.float32) * (1 - alpha) + patch[:, :, :3].astype(np.float32) * alpha).astype(np.uint8)


_PLATES: dict[tuple, np.ndarray] = {}
_MASKS: dict[tuple, np.ndarray] = {}
_SPRITES: dict[tuple, tuple[np.ndarray, tuple[float, float]]] = {}


def _plate(canvas_w: int, canvas_h: int, background: str, padding: float, radius: float) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    key = (canvas_w, canvas_h, background, round(padding, 2), round(radius, 2))
    cached = _PLATES.get(key)
    dx, dy, dw, dh = _window(canvas_w, canvas_h, padding)
    if cached is not None:
        return cached, (dx, dy, dw, dh)
    red, green, blue = parse_hex(background)
    frame = np.zeros((canvas_h, canvas_w, 3), np.uint8)
    frame[:, :] = (blue, green, red)
    shadow = frame.copy()
    x0, y0, x1, y1 = int(dx), int(dy + 16), int(dx + dw), int(dy + dh + 16)
    cv2.rectangle(shadow, (x0, y0), (x1, y1), (0, 0, 0), -1)
    shadow = cv2.GaussianBlur(shadow, (0, 0), 40)
    frame = cv2.addWeighted(frame, 0.65, shadow, 0.35, 0)
    _PLATES[key] = frame
    return frame, (dx, dy, dw, dh)


def _mask_for(width: int, height: int, radius: float) -> np.ndarray:
    key = (width, height, int(radius))
    cached = _MASKS.get(key)
    if cached is not None:
        return cached
    mask = _rounded_mask(width, height, int(max(0, min(radius, min(width, height) / 2))))
    _MASKS[key] = mask
    return mask


def _scaled_sprite(sprite: tuple[np.ndarray, np.ndarray], cursor_scale: float) -> tuple[np.ndarray, tuple[float, float]]:
    image, hotspot = sprite
    key = (id(image), round(cursor_scale, 2))
    cached = _SPRITES.get(key)
    if cached is not None:
        return cached
    if abs(cursor_scale - 1) <= 0.02:
        scaled = (image, (float(hotspot[0]), float(hotspot[1])))
    else:
        resized = cv2.resize(
            image,
            (max(1, int(image.shape[1] * cursor_scale)), max(1, int(image.shape[0] * cursor_scale))),
            interpolation=cv2.INTER_LINEAR,
        )
        scaled = (resized, (float(hotspot[0]) * cursor_scale, float(hotspot[1]) * cursor_scale))
    _SPRITES[key] = scaled
    return scaled


def _paste_window(frame: np.ndarray, window: np.ndarray, mask: np.ndarray, x: int, y: int, radius: int) -> None:
    """Copy the opaque interior and blend only the rounded border."""
    height, width = frame.shape[:2]
    wh, ww = window.shape[:2]
    if x < 0 or y < 0 or x + ww > width or y + wh > height:
        _paste_masked(frame, window, mask, x, y)
        return
    if radius <= 0 or mask.min() == 255:
        frame[y: y + wh, x: x + ww] = window
        return
    r = min(radius, wh // 2, ww // 2)
    frame[y + r: y + wh - r, x: x + ww] = window[r: wh - r]
    alpha = (mask.astype(np.float32) / 255.0)[:, :, None]
    for y0, y1, x0, x1 in (
        (0, r, 0, ww),
        (wh - r, wh, 0, ww),
        (r, wh - r, 0, r),
        (r, wh - r, ww - r, ww),
    ):
        if y1 <= y0 or x1 <= x0:
            continue
        band_a = alpha[y0:y1, x0:x1]
        src = window[y0:y1, x0:x1].astype(np.float32)
        dst = frame[y + y0: y + y1, x + x0: x + x1].astype(np.float32)
        frame[y + y0: y + y1, x + x0: x + x1] = (dst * (1 - band_a) + src * band_a).astype(np.uint8)


def render_frame(
    source: np.ndarray,
    sample: CameraSample,
    cursor: Point | None,
    cursor_scale: float,
    ripples: list[tuple[float, float, float]],
    background: str,
    padding: float,
    radius: float,
    canvas: tuple[int, int],
    blurs: list[dict] | None = None,
    sprite: tuple[np.ndarray, np.ndarray] | None = None,
    cursor_opacity: float = 1.0,
) -> np.ndarray:
    masked = apply_blurs(source, blurs or [])
    canvas_w, canvas_h = canvas
    plate, (dx, dy, dw, dh) = _plate(canvas_w, canvas_h, background, padding, radius)
    frame = plate.copy()
    window = _warp_window(masked, sample, int(round(dw)), int(round(dh)))
    corner = int(max(0, min(radius, min(window.shape[:2]) / 2)))
    mask = _mask_for(window.shape[1], window.shape[0], corner)
    _paste_window(frame, window, mask, int(round(dx)), int(round(dy)), corner)
    source_size = (masked.shape[1], masked.shape[0])
    dest = (dx, dy, dw, dh)
    for rx, ry, age in ripples:
        if age < 0 or age > 0.45:
            continue
        point = map_point(Point(rx, ry), sample, source_size, dest)
        grow = age / 0.45
        radius_px = 10 + (44 - 10) * (1 - (1 - grow) ** 2)
        fade = 0.9 * (1 - grow)
        _ripple(frame, point.x, point.y, radius_px, fade)
    if cursor is not None and sprite is not None and cursor_opacity > 0.01:
        _stamp_cursor(frame, sprite, cursor, cursor_scale, cursor_opacity, sample, source_size, dest)
    return frame


def _stamp_cursor(frame, sprite, cursor: Point, cursor_scale: float, opacity: float, sample: CameraSample, source_size, dest) -> None:
    image, hotspot = _scaled_sprite(sprite, cursor_scale)
    point = map_point(cursor, sample, source_size, dest)
    _blit(
        frame,
        image,
        int(round(point.x - hotspot[0])),
        int(round(point.y - hotspot[1])),
        opacity=opacity,
    )


def _ripple(frame: np.ndarray, x: float, y: float, radius_px: float, fade: float) -> None:
    rad = int(radius_px) + 3
    height, width = frame.shape[:2]
    x0 = max(0, int(x) - rad)
    y0 = max(0, int(y) - rad)
    x1 = min(width, int(x) + rad)
    y1 = min(height, int(y) + rad)
    if x1 <= x0 or y1 <= y0:
        return
    roi = frame[y0:y1, x0:x1]
    overlay = roi.copy()
    center = (int(round(x)) - x0, int(round(y)) - y0)
    cv2.circle(overlay, center, int(radius_px), (229, 70, 79), 4)
    cv2.circle(overlay, center, max(1, int(radius_px * 0.55)), (241, 102, 99), -1)
    frame[y0:y1, x0:x1] = cv2.addWeighted(overlay, fade, roi, 1 - fade * 0.35, 0)


def _warp_window(source: np.ndarray, sample: CameraSample, dest_w: int, dest_h: int) -> np.ndarray:
    height, width = source.shape[:2]
    crop = crop_rect(sample.cx, sample.cy, sample.scale, (width, height))
    matrix = np.array([
        [crop.w / dest_w, 0, crop.x],
        [0, crop.h / dest_h, crop.y],
    ], np.float32)
    return cv2.warpAffine(
        source,
        matrix,
        (dest_w, dest_h),
        flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _paste_masked(frame: np.ndarray, window: np.ndarray, mask: np.ndarray, x: int, y: int) -> None:
    height, width = frame.shape[:2]
    wh, ww = window.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + ww), min(height, y + wh)
    if x1 <= x0 or y1 <= y0:
        return
    sx0, sy0 = x0 - x, y0 - y
    patch = window[sy0: sy0 + (y1 - y0), sx0: sx0 + (x1 - x0)]
    alpha = mask[sy0: sy0 + (y1 - y0), sx0: sx0 + (x1 - x0)].astype(np.float32) / 255.0
    alpha = alpha[:, :, None]
    region = frame[y0:y1, x0:x1]
    region[:] = (region.astype(np.float32) * (1 - alpha) + patch.astype(np.float32) * alpha).astype(np.uint8)


def ripple_ages(presses: list[dict], output_t: float) -> list[tuple[float, float, float]]:
    ages = []
    for press in presses:
        age = output_t - float(press["output"])
        if 0 <= age <= 0.45:
            ages.append((float(press["x"]), float(press["y"]), age))
    return ages


def encode_frames(frames: list[np.ndarray], path: str, fps: int, crf: int = 17) -> None:
    if not frames:
        raise RuntimeError("No frames to encode")
    height, width = frames[0].shape[:2]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    command = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "-",
        "-an",
        "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-crf", str(crf), "-preset", "veryfast", "-movflags", "+faststart",
        path,
    ]
    process = subprocess.run(command, input=b"".join(frame.tobytes() for frame in frames), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if process.returncode != 0:
        raise RuntimeError(process.stderr.decode("utf-8", errors="replace")[-500:])


def render_movie(
    source_at: FrameSource,
    source_size: tuple[int, int],
    travels: list[Travel],
    camera: list[CameraSample],
    presses: list[dict],
    path: str,
    background: str = "#1E1B4B",
    padding: float = 64,
    radius: float = 16,
    preview: bool = False,
    blurs: list[dict] | None = None,
    crf: int = 18,
    cursor_scale: float = 1.6,
    motion_blur: str = "off",
    caption_at: Callable[[float], str] | None = None,
    title_at: Callable[[float], str] | None = None,
    step_bounds: list[tuple[float, float]] | None = None,
) -> dict:
    import sys
    import threading
    import time
    from queue import Full, Queue

    from screenreel_make.cursor import cursor_opacity

    canvas_w, canvas_h, fps = output_size(preview)
    sprite = _cursor_sprite(44)
    idle = travels[0].start if travels else Point(source_size[0] / 2, source_size[1] / 2)
    total = len(camera)
    started = time.perf_counter()
    process = None
    writer = None
    pending: Queue = Queue(maxsize=2)
    write_error: list[BaseException] = []
    frames = 0
    supersamples = 0
    boundaries = [start for start, _end in (step_bounds or []) if start > 0.05]
    fade_left = 0
    previous_frame: np.ndarray | None = None
    try:
        for index, sample in enumerate(camera):
            source = source_at(sample.t)
            if source.shape[1] != source_size[0] or source.shape[0] != source_size[1]:
                source = cv2.resize(source, source_size)

            def paint(at: CameraSample, when: float, draw_cursor: bool = True) -> np.ndarray:
                cursor, press_scale = sample_cursor(travels, when, idle)
                return render_frame(
                    source,
                    at,
                    cursor if draw_cursor else None,
                    press_scale * cursor_scale,
                    ripple_ages(presses, when),
                    background,
                    padding,
                    radius,
                    (canvas_w, canvas_h),
                    blurs=blurs,
                    sprite=sprite,
                    cursor_opacity=cursor_opacity(travels, when) if draw_cursor else 0.0,
                )

            # `cursor` blurs only the sprite. `all` resamples the whole frame.
            if motion_blur in {"all", "on"} and _needs_blur(camera, index):
                frame = paint(sample, sample.t)
                acc = frame.astype(np.float32)
                for sub in (-0.375, -0.125, 0.125, 0.375):
                    acc += paint(sample, sample.t + sub / fps)
                    supersamples += 1
                frame = (acc / 5).astype(np.uint8)
            elif motion_blur == "cursor" and _cursor_fast(travels, sample.t, fps, idle):
                frame = paint(sample, sample.t, draw_cursor=False)
                dx, dy, dw, dh = _window(canvas_w, canvas_h, padding)
                dest = (dx, dy, dw, dh)
                for sub, weight in zip((-0.375, -0.125, 0.125, 0.375), (0.15, 0.35, 0.35, 0.15)):
                    when = sample.t + sub / fps
                    cursor, press_scale = sample_cursor(travels, when, idle)
                    _stamp_cursor(
                        frame,
                        sprite,
                        cursor,
                        press_scale * cursor_scale,
                        cursor_opacity(travels, when) * weight,
                        sample,
                        source_size,
                        dest,
                    )
                    supersamples += 1
            else:
                frame = paint(sample, sample.t)
            if index > 0 and fade_left == 0 and _hard_jump(camera[index - 1], sample):
                fade_left = 6
            if fade_left > 0 and previous_frame is not None:
                mix = fade_left / 6
                frame = cv2.addWeighted(frame, 1 - mix, previous_frame, mix, 0)
                fade_left -= 1
            elif boundaries and previous_frame is not None:
                gap = min(abs(sample.t - mark) for mark in boundaries)
                if gap <= 6 / fps:
                    mix = gap / (6 / fps)
                    frame = cv2.addWeighted(frame, 0.5 + 0.5 * mix, previous_frame, 0.5 - 0.5 * mix, 0)
            caption = caption_at(sample.t) if caption_at else ""
            if caption:
                _burn_caption(frame, caption)
            title = title_at(sample.t) if title_at else ""
            if title:
                _burn_caption(frame, title, top=True)
            previous_frame = frame
            if process is None:
                process = _encoder(path, frame.shape[1], frame.shape[0], fps, crf)

                def _write() -> None:
                    assert process is not None and process.stdin is not None
                    try:
                        while True:
                            item = pending.get()
                            if item is None:
                                return
                            process.stdin.write(item)
                    except Exception as exc:  # noqa: BLE001 - re-raised after the loop
                        write_error.append(exc)

                writer = threading.Thread(target=_write, daemon=True)
                writer.start()
            # A 1080p frame is larger than the pipe buffer, so writing on this
            # thread would stall the next composite. The queue lets encode
            # overlap the next frame.
            blob = frame.tobytes()
            while True:
                if write_error:
                    raise RuntimeError(str(write_error[0]))
                try:
                    pending.put(blob, timeout=0.5)
                    break
                except Full:
                    continue
            frames += 1
            if frames == 1 or frames % 30 == 0 or frames == total:
                _progress(frames, total, started)
        if process is None:
            raise RuntimeError("No frames to encode")
        pending.put(None)
        if writer is not None:
            writer.join()
        if write_error:
            raise RuntimeError(str(write_error[0]))
        assert process.stdin is not None
        process.stdin.close()
        err = process.stderr.read() if process.stderr else b""
        code = process.wait()
        process = None
        if code != 0:
            raise RuntimeError(err.decode("utf-8", errors="replace")[-500:])
    finally:
        if writer is not None and writer.is_alive():
            try:
                pending.put_nowait(None)
            except Full:
                pass
        if process is not None and process.poll() is None:
            process.kill()
    elapsed = time.perf_counter() - started
    print(f"render done {frames} frames in {elapsed:.1f}s", file=sys.stderr, flush=True)
    return {
        "width": canvas_w,
        "height": canvas_h,
        "fps": fps,
        "frames": frames,
        "path": path,
        "elapsed_s": round(elapsed, 3),
        "supersamples": supersamples,
    }


def _needs_blur(camera: list[CameraSample], index: int) -> bool:
    if index == 0:
        return False
    previous = camera[index - 1]
    current = camera[index]
    dt = max(current.t - previous.t, 1e-4)
    pan = math.hypot(current.cx - previous.cx, current.cy - previous.cy) / dt
    zoom = abs(current.scale - previous.scale) / dt
    return pan > 400 or zoom > 0.35


def _cursor_fast(travels: list[Travel], t: float, fps: int, idle: Point) -> bool:
    if not travels:
        return False
    before = sample_cursor(travels, t - 1.0 / fps, idle)[0]
    after = sample_cursor(travels, t, idle)[0]
    return before.distance(after) * fps > 600


def _hard_jump(previous: CameraSample, current: CameraSample) -> bool:
    return math.hypot(current.cx - previous.cx, current.cy - previous.cy) > 80


def _encoder(path: str, width: int, height: int, fps: int, crf: int) -> subprocess.Popen:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return subprocess.Popen(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", str(fps),
            "-i", "-", "-an",
            "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-crf", str(crf), "-preset", "veryfast", "-movflags", "+faststart",
            path,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def _progress(done: int, total: int, started: float) -> None:
    import sys
    import time

    elapsed = max(time.perf_counter() - started, 1e-3)
    rate = done / elapsed
    left = (total - done) / rate if rate else 0
    print(f"render {100 * done / max(total, 1):.0f}% eta {left:.0f}s", file=sys.stderr, flush=True)


_CAPTIONS: dict[tuple, np.ndarray] = {}


def _burn_caption(frame: np.ndarray, text: str, top: bool = False) -> None:
    from PIL import Image, ImageDraw, ImageFont

    lines = [line for line in text.split("\n") if line][:2]
    if not lines:
        return
    key = (frame.shape[1], tuple(lines), top)
    overlay = _CAPTIONS.get(key)
    if overlay is None:
        font_path = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
        size = max(18, frame.shape[1] // 54)
        font = ImageFont.truetype(font_path, size) if os.path.exists(font_path) else ImageFont.load_default()
        dummy = Image.new("RGBA", (frame.shape[1], 10))
        draw = ImageDraw.Draw(dummy)
        heights = []
        widths = []
        for line in lines:
            box = draw.textbbox((0, 0), line, font=font)
            widths.append(box[2] - box[0])
            heights.append(box[3] - box[1])
        pad = 14
        strip_h = sum(heights) + pad * (len(lines) + 1)
        strip = Image.new("RGBA", (frame.shape[1], strip_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(strip)
        y = pad
        for line, width, height in zip(lines, widths, heights):
            x = (frame.shape[1] - width) // 2
            draw.rounded_rectangle((x - 12, y - 6, x + width + 12, y + height + 8), radius=8, fill=(15, 23, 42, 180))
            draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
            y += height + pad
        overlay = cv2.cvtColor(np.array(strip), cv2.COLOR_RGBA2BGRA)
        _CAPTIONS[key] = overlay
    sh, sw = overlay.shape[:2]
    y0 = max(12, frame.shape[0] // 28) if top else frame.shape[0] - sh - max(12, frame.shape[0] // 28)
    x0 = 0
    if y0 < 0:
        return
    alpha = overlay[:, :, 3:4].astype(np.float32) / 255.0
    region = frame[y0: y0 + sh, x0: x0 + sw]
    region[:] = (region.astype(np.float32) * (1 - alpha) + overlay[:, :, :3].astype(np.float32) * alpha).astype(np.uint8)


def probe(path: str) -> dict:
    command = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate,codec_name,profile,pix_fmt,nb_frames",
        "-of", "json",
        path,
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        return {}
    import json
    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams") or [{}]
    return streams[0]
