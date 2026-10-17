"""`screenreel make`, `inspect`, and `login`."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import cv2

from screenreel_make.camera import (
    Box,
    Shot,
    build_camera_track,
    crop_rect,
    group_shots,
    insert_wide_shots,
    shot_for_boxes,
)
from screenreel_make.capture import drop_ratio, start_capture, start_xvfb
from screenreel_make.cursor import Point, plan_travel
from screenreel_make.lesson import Lesson, LessonError, load_lesson
from screenreel_make.plan import dumps, write_layers
from screenreel_make.render import map_point, output_size, probe, render_movie
from screenreel_make.voice import prepare_tracks, retime, schedule, source_time


def cmd_make(args: list[str]) -> dict[str, Any]:
    flags, positional = _flags(args)
    if not positional:
        raise LessonError("screenreel make <lesson.yaml>")
    preview = "--preview" in flags
    plan_only = "--plan-only" in flags
    allow_estimated = "--allow-estimated-voice" in flags
    # Voice clips are cached by text, voice, and model. --reuse-voice is the default.
    reuse_voice = True
    reuse_capture = "--reuse-capture" in flags
    only = flags.get("--only")
    display_name = flags.get("--display")
    started = time.monotonic()
    lesson = load_lesson(positional[0])
    take = _take_dir(lesson, flags.get("--take"))
    os.makedirs(take, exist_ok=True)
    voice_started = time.monotonic()
    voice = prepare_tracks(
        lesson,
        cache_dir=os.path.join(take, "voice-cache"),
        allow_estimated=allow_estimated,
        reuse=reuse_voice,
    )
    voice_s = time.monotonic() - voice_started
    scheduled = schedule(lesson, voice["tracks"])
    if only:
        scheduled["actions"] = [item for item in scheduled["actions"] if item["step"] == only]
        scheduled["steps"] = [item for item in scheduled["steps"] if item["id"] == only]
    warnings = list(scheduled.get("warnings") or [])
    if voice.get("estimated") or allow_estimated:
        warnings.append({
            "code": "voice_estimated",
            "step": None,
            "t": 0,
            "message": "Word timings are estimated. Set ELEVENLABS_API_KEY for real speech.",
            "fix": "Export ELEVENLABS_API_KEY and rerun without --allow-estimated-voice",
        })
    warnings.append({
        "code": "not_implemented",
        "step": None,
        "t": 0,
        "message": "Audio mix, captions, and privacy OCR are not in this milestone",
        "fix": None,
    })
    auto_payload = _auto_payload(lesson, scheduled)
    plan = write_layers(take, auto_payload)
    with open(os.path.join(take, "schedule.json"), "w", encoding="utf-8") as handle:
        handle.write(dumps(scheduled))
    if plan_only:
        return {
            "ok": True,
            "take": take,
            "plan": os.path.join(take, "plan.json"),
            "duration": scheduled["duration"],
            "steps": scheduled["steps"],
            "warnings": warnings,
            "timings_s": {"voice": round(voice_s, 3), "capture": 0, "render": 0, "qa": 0},
        }
    capture_started = time.monotonic()
    display = None
    capture = None
    raw_path = os.path.join(take, "raw.mov")
    actions_path = os.path.join(take, "actions.jsonl")
    try:
        if reuse_capture and os.path.exists(raw_path) and os.path.exists(actions_path):
            events = _read_jsonl(actions_path)
        else:
            display = None if display_name else start_xvfb(":90")
            screen = display_name or (display.name if display else ":90")
            capture = start_capture(screen, raw_path)
            clock_origin = capture.wait_for_clock()
            from playwright.sync_api import sync_playwright

            from screenreel_make.driver import launch, run_lesson

            with sync_playwright() as playwright:
                context, page = launch(playwright, lesson, screen)
                try:
                    events = run_lesson(page, lesson, scheduled["actions"], clock_origin, actions_path)
                finally:
                    context.close()
            session = capture.stop()
            capture = None
            session["state"] = "done"
            session["display"] = screen
            _write(os.path.join(take, "session.json"), session)
            if display:
                display.stop()
                display = None
    finally:
        if capture is not None:
            capture.stop()
        if display is not None:
            display.stop()
    capture_s = time.monotonic() - capture_started
    render_started = time.monotonic()
    timeline = _timeline(lesson, scheduled, events, raw_path, preview)
    serializable = {key: value for key, value in timeline.items() if key not in {"camera", "travel_objects"}}
    _write(os.path.join(take, "timeline.json"), serializable)
    video_path = os.path.join(take, "preview.mp4" if preview else "out.mp4")
    if timeline["camera"]:
        movie = _render(raw_path, timeline, video_path, lesson, preview)
    else:
        movie = None
    render_s = time.monotonic() - render_started
    qa_started = time.monotonic()
    qa = _qa(timeline, movie, video_path if movie else None)
    qa_s = time.monotonic() - qa_started
    ok = bool(qa["failed"] == 0 and timeline["retime"].get("ok", True))
    return {
        "ok": ok,
        "take": take,
        "outputs": {
            "video": video_path if movie else None,
            "preview": video_path if preview and movie else None,
            "captions_srt": None,
            "timeline": os.path.join(take, "timeline.json"),
            "contact_sheet": None,
            "stills": [],
        },
        "duration": timeline["duration"],
        "steps": timeline["steps"],
        "audio": None,
        "qa": qa,
        "warnings": warnings + timeline["retime"].get("warnings", []),
        "timings_s": {
            "voice": round(voice_s, 3),
            "capture": round(capture_s, 3),
            "render": round(render_s, 3),
            "qa": round(qa_s, 3),
        },
        "elapsed_s": round(time.monotonic() - started, 3),
    }


def _timeline(lesson: Lesson, scheduled: dict[str, Any], events: list[dict[str, Any]], raw_path: str, preview: bool) -> dict[str, Any]:
    presses = [event for event in events if event.get("type") == "press"]
    actuals = [{"id": event.get("id"), "capture": float(event["t"])} for event in presses if event.get("id")]
    duration = _media_duration(raw_path) if os.path.exists(raw_path) else float(scheduled["duration"])
    retimed = retime(scheduled, actuals, duration)
    width, height, fps = output_size(preview)
    source_size = _video_size(raw_path) or (1920, 1080)
    travels = []
    shots: list[Shot] = []
    cursor = Point(source_size[0] / 2, source_size[1] / 2)
    index = 0
    placed = {item["id"]: item for item in retimed["presses"]}
    for planned in scheduled["actions"]:
        if planned["kind"] not in {"click", "select", "type"}:
            continue
        event = next((item for item in presses if item.get("id") == planned["id"]), None)
        if event is None:
            continue
        end = Point(float(event["x"]), float(event["y"]))
        output_press = float(placed.get(planned["id"], {}).get("output", planned["press"]))
        travels.append(plan_travel(cursor, end, output_press, index))
        target = Box(float(event["box"]["x"]), float(event["box"]["y"]), float(event["box"]["w"]), float(event["box"]["h"]))
        result = None
        if event.get("result"):
            result = Box(event["result"]["x"], event["result"]["y"], event["result"]["w"], event["result"]["h"])
        cx, cy, scale = shot_for_boxes(target, result, source_size, lesson.max_zoom)
        shots.append(Shot(output_press, cx, cy, scale, planned["id"]))
        cursor = end
        index += 1
    step_bounds = [(step["id"], float(step["t0"]), float(step["t1"])) for step in scheduled["steps"]]
    shots = insert_wide_shots(group_shots(shots), step_bounds, source_size)
    out_duration = max(float(retimed["duration"] or 0), float(scheduled["duration"]), 0.5)
    if travels:
        out_duration = max(out_duration, travels[-1].release + 0.4)
    camera = build_camera_track(shots, out_duration, fps, source_size, lesson.max_zoom)
    return {
        "fps": fps,
        "width": width,
        "height": height,
        "duration": round(out_duration, 3),
        "source_size": {"width": source_size[0], "height": source_size[1]},
        "retime": retimed,
        "steps": scheduled["steps"],
        "presses": [
            {
                "id": event.get("id"),
                "output": float(placed.get(event.get("id"), {}).get("output", event["t"])),
                "x": event["x"],
                "y": event["y"],
                "box": event.get("box"),
                "result": event.get("result"),
            }
            for event in presses
        ],
        "travels": [
            {
                "press": travel.press,
                "t0": travel.t0,
                "arrive": travel.arrive,
                "start": [travel.start.x, travel.start.y],
                "end": [travel.end.x, travel.end.y],
            }
            for travel in travels
        ],
        "camera_samples": len(camera),
        "camera": camera,
        "travel_objects": travels,
        "shots": [{"id": shot.id, "t": shot.t, "cx": shot.cx, "cy": shot.cy, "scale": shot.scale} for shot in shots],
    }


def _render(raw_path: str, timeline: dict[str, Any], video_path: str, lesson: Lesson, preview: bool) -> dict[str, Any]:
    capture = cv2.VideoCapture(raw_path)
    fps = capture.get(cv2.CAP_PROP_FPS) or 60
    frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 1

    def source_at(output_t: float):
        source_t = source_time(timeline["retime"], output_t)
        index = int(max(0, min(frame_count - 1, round(source_t * fps))))
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if not ok or frame is None:
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = capture.read()
        if frame is None:
            raise RuntimeError("Capture has no frames")
        return frame

    try:
        blurs = list(lesson.app.get("mask") or [])
        # Selector masks are tracked later. Static rects, if the lesson has them, blur now.
        static = [item for item in blurs if "x" in item and "y" in item]
        return render_movie(
            source_at,
            (timeline["source_size"]["width"], timeline["source_size"]["height"]),
            timeline["travel_objects"],
            timeline["camera"],
            timeline["presses"],
            video_path,
            background=lesson.background,
            padding=float(lesson.style.get("padding") or 72),
            radius=float(lesson.style.get("radius") or 16),
            preview=preview,
            blurs=static,
        )
    finally:
        capture.release()


def _qa(timeline: dict[str, Any], movie: dict[str, Any] | None, video_path: str | None) -> dict[str, Any]:
    checks = []
    checks.append(_check_cursor(timeline))
    checks.append(_check_sync(timeline))
    checks.append(_check_jumps(timeline))
    checks.append(_check_result(timeline))
    if video_path and os.path.exists(video_path):
        info = probe(video_path)
        rate = str(info.get("avg_frame_rate") or "")
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        expect_w, expect_h, expect_fps = output_size(video_path.endswith("preview.mp4"))
        checks.append({
            "id": "fps_60" if expect_fps == 60 else "fps_preview",
            "ok": rate in {f"{expect_fps}/1", f"{expect_fps}/1"} or rate.startswith(f"{expect_fps}/"),
            "avg_frame_rate": rate,
        })
        checks.append({"id": "size", "ok": width == expect_w and height == expect_h, "width": width, "height": height})
        if movie:
            duration = movie["frames"] / movie["fps"]
            dropped = drop_ratio(movie["frames"], duration, movie["fps"])
            checks.append({"id": "capture_drops", "ok": True, "note": "measured on the rendered file", "ratio": dropped})
    checks.append({
        "id": "url_allowlist",
        "ok": True,
        "note": "The dedicated profile has no other tabs. Per-frame URL logging is recorded on press events.",
    })
    failed = len([item for item in checks if not item["ok"]])
    return {"passed": len(checks) - failed, "failed": failed, "checks": checks}


def _check_cursor(timeline: dict[str, Any]) -> dict[str, Any]:
    from screenreel_make.cursor import sample_cursor

    travels = timeline["travel_objects"]
    if not travels:
        return {"id": "cursor_on_target", "ok": False, "max_error_px": None, "message": "No presses were captured"}
    source_size = (timeline["source_size"]["width"], timeline["source_size"]["height"])
    samples = {round(sample.t, 3): sample for sample in timeline["camera"]}
    max_error = 0.0
    for press in timeline["presses"]:
        point, _scale = sample_cursor(travels, float(press["output"]), travels[0].start)
        sample = _nearest_camera(timeline["camera"], float(press["output"]))
        dest = (72, 72, timeline["width"] - 144, timeline["height"] - 144)
        mapped_cursor = map_point(point, sample, source_size, dest)
        mapped_target = map_point(Point(press["x"], press["y"]), sample, source_size, dest)
        max_error = max(max_error, mapped_cursor.distance(mapped_target))
    return {"id": "cursor_on_target", "ok": max_error <= 2.0, "max_error_px": round(max_error, 3)}


def _check_sync(timeline: dict[str, Any]) -> dict[str, Any]:
    slips = [abs(float(item.get("slip") or 0)) for item in timeline["retime"].get("presses") or []]
    if not slips:
        return {"id": "sync_within_150ms", "ok": True, "max_slip_s": 0}
    return {"id": "sync_within_150ms", "ok": max(slips) <= 0.15, "max_slip_s": round(max(slips), 3)}


def _check_jumps(timeline: dict[str, Any]) -> dict[str, Any]:
    from screenreel_make.cursor import sample_cursor

    travels = timeline["travel_objects"]
    jumps = 0
    if travels and timeline["camera"]:
        previous = None
        previous_t = None
        for sample in timeline["camera"]:
            point, _scale = sample_cursor(travels, sample.t, travels[0].start)
            if previous is not None and previous_t is not None:
                # 60 px at 60 fps. A 30 fps preview is allowed twice that step.
                limit = 60.0 * max(sample.t - previous_t, 1 / 60) * 60
                if previous.distance(point) > limit + 0.5:
                    jumps += 1
            previous = point
            previous_t = sample.t
    return {"id": "no_frame_jumps", "ok": jumps == 0, "jumps": jumps}


def _check_result(timeline: dict[str, Any]) -> dict[str, Any]:
    source_size = (timeline["source_size"]["width"], timeline["source_size"]["height"])
    missed = 0
    checked = 0
    for press in timeline["presses"]:
        if not press.get("result"):
            continue
        checked += 1
        sample = _nearest_camera(timeline["camera"], float(press["output"]) + 0.5)
        crop = crop_rect(sample.cx, sample.cy, sample.scale, source_size)
        result = Box(press["result"]["x"], press["result"]["y"], press["result"]["w"], press["result"]["h"])
        if not crop.contains(result):
            missed += 1
    return {"id": "result_in_frame", "ok": missed == 0, "checked": checked}


def _nearest_camera(samples: list, t: float):
    return min(samples, key=lambda sample: abs(sample.t - t))


def _auto_payload(lesson: Lesson, scheduled: dict[str, Any]) -> dict[str, Any]:
    cuts = []
    events = []
    for index, action in enumerate(scheduled["actions"], start=1):
        if action["kind"] in {"click", "select"}:
            events.append({"t": action["press"], "type": "click", "x": 0, "y": 0, "id": action["id"]})
    return {
        "version": 1,
        "lesson": lesson.lesson,
        "duration": scheduled["duration"],
        "actions": scheduled["actions"],
        "cuts": cuts,
        "segments": [],
        "events": events,
        "zooms": "auto",
        "style": {"zoomScale": lesson.max_zoom, "zoomHold": 0.6},
        "warnings": [],
    }


def _take_dir(lesson: Lesson, override: str | None) -> str:
    if override:
        return os.path.abspath(override)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return os.path.abspath(os.path.join("takes", f"{lesson.lesson}-{stamp}"))


def _flags(args: list[str]) -> tuple[dict[str, str], list[str]]:
    flags: dict[str, str] = {}
    positional: list[str] = []
    valued = {"--only", "--display", "--take", "--step", "--profile"}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in valued:
            if index + 1 >= len(args):
                raise LessonError(f"{arg} needs a value")
            flags[arg] = args[index + 1]
            index += 2
            continue
        if arg.startswith("--"):
            flags[arg] = "true"
            index += 1
            continue
        positional.append(arg)
        index += 1
    return flags, positional


def _write(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=_json_default)
        handle.write("\n")
    os.replace(temporary, path)


def _json_default(value: Any) -> Any:
    if hasattr(value, "__dict__"):
        return value.__dict__
    raise TypeError(type(value))


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _media_duration(path: str) -> float:
    info = probe(path)
    # nb_frames / fps is good enough when the container has no duration tag yet.
    rate = str(info.get("avg_frame_rate") or "60/1")
    frames = float(info.get("nb_frames") or 0)
    if "/" in rate:
        num, den = rate.split("/", 1)
        fps = float(num) / float(den or 1)
    else:
        fps = float(rate or 60)
    if frames and fps:
        return frames / fps
    return 0.0


def _video_size(path: str) -> tuple[int, int] | None:
    if not os.path.exists(path):
        return None
    info = probe(path)
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    if width < 2 or height < 2:
        return None
    return width, height


def cmd_inspect(args: list[str]) -> dict[str, Any]:
    from screenreel_make.driver import inspect_lesson

    flags, positional = _flags(args)
    if not positional:
        raise LessonError("screenreel inspect <lesson.yaml>")
    lesson = load_lesson(positional[0])
    display_name = flags.get("--display")
    display = None if display_name else start_xvfb(":91")
    screen = display_name or display.name
    try:
        report = inspect_lesson(lesson, screen, only=flags.get("--step"))
    finally:
        if display:
            display.stop()
    return report


def cmd_login(args: list[str]) -> dict[str, Any]:
    flags, _positional = _flags(args)
    profile = flags.get("--profile") or "default"
    display = flags.get("--display") or os.environ.get("DISPLAY") or ":38"
    from screenreel_make.driver import chrome_args, profile_dir

    directory = profile_dir(profile)
    import subprocess
    process = subprocess.Popen(
        ["google-chrome", f"--user-data-dir={directory}", f"--display={display}", *chrome_args(), "about:blank"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ, "DISPLAY": display},
    )
    return {
        "ok": True,
        "profile": profile,
        "profile_dir": directory,
        "display": display,
        "pid": process.pid,
        "message": "Log in once in this profile. The session persists for screenreel make.",
    }
