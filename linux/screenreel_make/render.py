"""Output-time compositor.

The camera, cursor, and ripple are sampled on the output clock. Speed ramps
only choose which source frame is shown; they do not shorten an ease.
"""

from __future__ import annotations

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


def _blit(frame: np.ndarray, sprite: np.ndarray, origin_x: int, origin_y: int) -> None:
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
    alpha = patch[:, :, 3:4].astype(np.float32) / 255.0
    region[:] = (region.astype(np.float32) * (1 - alpha) + patch[:, :, :3].astype(np.float32) * alpha).astype(np.uint8)


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
) -> np.ndarray:
    masked = apply_blurs(source, blurs or [])
    canvas_w, canvas_h = canvas
    red, green, blue = parse_hex(background)
    frame = np.zeros((canvas_h, canvas_w, 3), np.uint8)
    frame[:, :] = (blue, green, red)
    dx, dy, dw, dh = _window(canvas_w, canvas_h, padding)
    # Shadow: blur 40, y offset 16, 35% black.
    shadow = frame.copy()
    x0, y0, x1, y1 = int(dx), int(dy + 16), int(dx + dw), int(dy + dh + 16)
    cv2.rectangle(shadow, (x0, y0), (x1, y1), (0, 0, 0), -1)
    shadow = cv2.GaussianBlur(shadow, (0, 0), 40)
    frame = cv2.addWeighted(frame, 0.65, shadow, 0.35, 0)
    window = _warp_window(masked, sample, int(round(dw)), int(round(dh)))
    mask = _rounded_mask(window.shape[1], window.shape[0], int(max(0, min(radius, min(window.shape[:2]) / 2))))
    y_a, x_a = int(round(dy)), int(round(dx))
    _paste_masked(frame, window, mask, x_a, y_a)
    # Ripples and cursor live in output pixels, after the camera.
    source_size = (masked.shape[1], masked.shape[0])
    dest = (dx, dy, dw, dh)
    for rx, ry, age in ripples:
        if age < 0 or age > 0.45:
            continue
        point = map_point(Point(rx, ry), sample, source_size, dest)
        grow = age / 0.45
        radius_px = 10 + (44 - 10) * (1 - (1 - grow) ** 2)
        fade = 0.9 * (1 - grow)
        overlay = frame.copy()
        cv2.circle(overlay, (int(round(point.x)), int(round(point.y))), int(radius_px), (229, 70, 79), 4)
        cv2.circle(overlay, (int(round(point.x)), int(round(point.y))), max(1, int(radius_px * 0.55)), (241, 102, 99), -1)
        frame = cv2.addWeighted(overlay, fade, frame, 1 - fade * 0.35, 0)
    if cursor is not None and sprite is not None:
        image, hotspot = sprite
        scaled = image
        if abs(cursor_scale - 1) > 0.02:
            scaled = cv2.resize(
                image,
                (max(1, int(image.shape[1] * cursor_scale)), max(1, int(image.shape[0] * cursor_scale))),
                interpolation=cv2.INTER_LINEAR,
            )
            hotspot = (hotspot[0] * cursor_scale, hotspot[1] * cursor_scale)
        point = map_point(cursor, sample, source_size, dest)
        _blit(frame, scaled, int(round(point.x - hotspot[0])), int(round(point.y - hotspot[1])))
    return frame


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
    padding: float = 72,
    radius: float = 16,
    preview: bool = False,
    blurs: list[dict] | None = None,
    crf: int = 17,
) -> dict:
    canvas_w, canvas_h, fps = output_size(preview)
    sprite = _cursor_sprite(44)
    frames = []
    idle = travels[0].start if travels else Point(source_size[0] / 2, source_size[1] / 2)
    for sample in camera:
        source = source_at(sample.t)
        if source.shape[1] != source_size[0] or source.shape[0] != source_size[1]:
            source = cv2.resize(source, source_size)
        cursor, press_scale = sample_cursor(travels, sample.t, idle)
        frame = render_frame(
            source,
            sample,
            cursor,
            press_scale,
            ripple_ages(presses, sample.t),
            background,
            padding,
            radius,
            (canvas_w, canvas_h),
            blurs=blurs,
            sprite=sprite,
        )
        frames.append(frame)
    encode_frames(frames, path, fps, crf=crf)
    return {"width": canvas_w, "height": canvas_h, "fps": fps, "frames": len(frames), "path": path}


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
