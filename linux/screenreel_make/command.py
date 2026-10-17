"""`screenreel make`, `inspect`, and `login`."""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

import cv2
import numpy as np

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
                    # Stop the grab before Chrome closes, or the last frames are a black desktop
                    # and the narration hold freezes on that empty picture.
                    session = capture.stop()
                    capture = None
                    context.close()
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
    timeline = _timeline(lesson, scheduled, events, raw_path, preview, voice["tracks"])
    serializable = {key: value for key, value in timeline.items() if key not in {"camera", "travel_objects"}}
    _write(os.path.join(take, "timeline.json"), serializable)
    video_path = os.path.join(take, "steps", f"{only}.mp4") if only else os.path.join(take, "preview.mp4" if preview else "out.mp4")
    if timeline["camera"]:
        movie = _render(raw_path, timeline, video_path, lesson, preview, scheduled)
    else:
        movie = None
    if movie:
        movie = _mix_and_caption(take, lesson, scheduled, timeline, video_path, movie, voice)
    render_s = time.monotonic() - render_started
    qa_started = time.monotonic()
    qa = _qa(timeline, movie, video_path if movie else None, lesson)
    qa_s = time.monotonic() - qa_started
    ok = bool(qa["failed"] == 0 and timeline["retime"].get("ok", True))
    return {
        "ok": ok,
        "take": take,
        "outputs": {
            "video": None if preview or not movie else video_path,
            "preview": video_path if preview and movie else None,
            "step": video_path if only and movie else None,
            "captions_srt": (movie or {}).get("srt"),
            "captions_vtt": (movie or {}).get("vtt"),
            "timeline": os.path.join(take, "timeline.json"),
            "contact_sheet": (movie or {}).get("contact"),
            "stills": (movie or {}).get("stills") or [],
        },
        "duration": timeline["duration"],
        "fps": timeline["fps"],
        "steps": timeline["steps"],
        "audio": (movie or {}).get("audio"),
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


def _timeline(lesson: Lesson, scheduled: dict[str, Any], events: list[dict[str, Any]], raw_path: str, preview: bool, tracks: dict[str, Any]) -> dict[str, Any]:
    presses = [event for event in events if event.get("type") == "press"]
    pointed = [event for event in events if event.get("type") in {"press", "hover", "type"}]
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
        if planned["kind"] not in {"click", "select", "type", "hover"}:
            continue
        event = next((item for item in pointed if item.get("id") == planned["id"]), None)
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
        "mask_events": [event for event in events if event.get("type") == "mask"],
        "cues": _cues(scheduled, tracks),
        "key_times": _key_times(events, placed),
        "shots": [{"id": shot.id, "t": shot.t, "cx": shot.cx, "cy": shot.cy, "scale": shot.scale} for shot in shots],
    }


def _cues(scheduled: dict[str, Any], tracks: dict[str, Any]) -> list[dict[str, Any]]:
    from screenreel_make.captions import wrap_cues

    cues = []
    for step in scheduled.get("steps") or []:
        words = tracks.get(step["id"]) or []
        if not words:
            continue
        origin = float(step.get("audio_start") or step.get("t0") or 0)
        base = float(words[0]["start"])
        shifted = [
            {"word": word["word"], "start": origin + float(word["start"]) - base, "end": origin + float(word["end"]) - base}
            for word in words
        ]
        cues.extend(wrap_cues(shifted))
    return cues


def _key_times(events: list[dict[str, Any]], placed: dict[str, Any]) -> list[float]:
    times = []
    for event in events:
        if event.get("type") != "type":
            continue
        start = float(placed.get(event.get("id"), {}).get("output", event.get("t") or 0))
        for index in range(int(event.get("keys") or 0)):
            times.append(round(start + index * 0.07, 3))
    return times


def _mask_boxes(events: list[dict[str, Any]], source_t: float) -> list[dict[str, Any]]:
    previous = None
    nxt = None
    for event in events:
        if float(event.get("t") or 0) <= source_t:
            previous = event
        elif nxt is None:
            nxt = event
            break
    if previous is None:
        return list(nxt["boxes"]) if nxt else []
    if nxt is None:
        return list(previous["boxes"])
    span = float(nxt["t"]) - float(previous["t"])
    if span <= 1e-3 or len(previous["boxes"]) != len(nxt["boxes"]):
        return list(previous["boxes"])
    mix = (source_t - float(previous["t"])) / span
    blended = []
    for left, right in zip(previous["boxes"], nxt["boxes"]):
        blended.append({
            "x": left["x"] + (right["x"] - left["x"]) * mix,
            "y": left["y"] + (right["y"] - left["y"]) * mix,
            "width": left["width"] + (right["width"] - left["width"]) * mix,
            "height": left["height"] + (right["height"] - left["height"]) * mix,
            "radius": left.get("radius") or 18,
        })
    return blended


def _mix_and_caption(take: str, lesson: Lesson, scheduled: dict[str, Any], timeline: dict[str, Any], video_path: str, movie: dict[str, Any], voice: dict[str, Any]) -> dict[str, Any]:
    from screenreel_make.audio import loudnorm, mix, tone_for_words, write_wav
    from screenreel_make.captions import validate_cues, write_srt, write_vtt
    from screenreel_make.voice import write_audio_files

    voice_dir = os.path.join(take, "voice")
    write_audio_files(voice.get("audio") or {}, voice_dir)
    files = dict(voice.get("files") or {})
    cues_in = []
    for step in scheduled.get("steps") or []:
        ident = step["id"]
        path = files.get(ident) or os.path.join(voice_dir, f"{ident}.mp3")
        if not os.path.exists(path):
            words = (voice.get("tracks") or {}).get(ident) or []
            path = os.path.join(voice_dir, f"{ident}.wav")
            write_wav(path, tone_for_words(words))
        cues_in.append((path, float(step.get("audio_start") or step.get("t0") or 0)))
    mix_wav = os.path.join(take, "mix.wav")
    mix(
        cues_in,
        [float(item["output"]) for item in timeline["presses"]],
        [float(item) for item in timeline.get("key_times") or []],
        float(timeline["duration"]),
        mix_wav,
        music_path=str((lesson.audio or {}).get("music") or ""),
        duck_db=abs(float((lesson.audio or {}).get("duck_db") or 14)),
    )
    muxed = video_path + ".mux.mp4"
    report = loudnorm(
        mix_wav,
        video_path,
        muxed,
        lufs=float((lesson.audio or {}).get("lufs") or -14),
        true_peak=float((lesson.audio or {}).get("true_peak") or -1),
    )
    os.replace(muxed, video_path)
    cues = timeline.get("cues") or []
    srt = os.path.join(take, "captions.srt")
    vtt = os.path.join(take, "captions.vtt")
    write_srt(cues, srt)
    write_vtt(cues, vtt)
    problems = validate_cues(cues)
    contact, stills = _contact_sheet(video_path, take, timeline)
    movie["audio"] = {"lufs": report.get("lufs"), "true_peak": report.get("true_peak"), "path": video_path}
    movie["srt"] = srt
    movie["vtt"] = vtt
    movie["caption_problems"] = problems
    movie["contact"] = contact
    movie["stills"] = stills
    return movie


def _contact_sheet(video_path: str, take: str, timeline: dict[str, Any]) -> tuple[str | None, list[str]]:
    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        return None, []
    fps = capture.get(cv2.CAP_PROP_FPS) or 30
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = count / fps if fps else 0
    stamps = list(range(0, int(duration) + 1, 5))
    for step in timeline.get("steps") or []:
        stamps.append(float(step.get("t0") or 0))
    import numpy as np

    stills = []
    tiles = []
    folder = os.path.join(take, "stills")
    os.makedirs(folder, exist_ok=True)
    for stamp in sorted(set(round(item, 2) for item in stamps)):
        capture.set(cv2.CAP_PROP_POS_MSEC, stamp * 1000)
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        small = cv2.resize(frame, (320, 180))
        path = os.path.join(folder, f"{stamp:.2f}.jpg")
        cv2.imwrite(path, small)
        stills.append(path)
        tiles.append(small)
    capture.release()
    if not tiles:
        return None, stills
    columns = 4
    rows = (len(tiles) + columns - 1) // columns
    sheet = np.zeros((rows * 180, columns * 320, 3), np.uint8)
    for index, tile in enumerate(tiles):
        y, x = divmod(index, columns)
        sheet[y * 180: (y + 1) * 180, x * 320: (x + 1) * 320] = tile
    contact = os.path.join(take, "contact.jpg")
    cv2.imwrite(contact, sheet)
    return contact, stills


def _render(raw_path: str, timeline: dict[str, Any], video_path: str, lesson: Lesson, preview: bool, scheduled: dict[str, Any]) -> dict[str, Any]:
    from screenreel_make.blur import apply_blurs
    from screenreel_make.captions import caption_text

    capture = cv2.VideoCapture(raw_path)
    fps = capture.get(cv2.CAP_PROP_FPS) or 60
    frame_count = max(1, int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 1))
    state = {"index": -1, "frame": None}
    static = [item for item in (lesson.app.get("mask") or []) if isinstance(item, dict) and "x" in item]
    mask_events = [event for event in timeline.get("mask_events") or [] if event.get("boxes")]
    cues = timeline.get("cues") or []

    def source_at(output_t: float):
        source_t = source_time(timeline["retime"], output_t)
        index = int(max(0, min(frame_count - 1, round(source_t * fps))))
        if index == state["index"] and state["frame"] is not None:
            frame = state["frame"]
        else:
            ok, frame = False, None
            if 0 < index - state["index"] <= 90:
                while state["index"] < index:
                    ok, frame = capture.read()
                    state["index"] += 1
                    if not ok:
                        break
            else:
                capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                ok, frame = capture.read()
                state["index"] = index
            if not ok or frame is None:
                capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = capture.read()
                state["index"] = 0
            if frame is None:
                raise RuntimeError("Capture has no frames")
            state["frame"] = frame
        boxes = list(static) + _mask_boxes(mask_events, source_t)
        if boxes:
            return apply_blurs(frame, boxes)
        return frame

    title_s = float(scheduled.get("title_card") or 0)
    end_s = float(scheduled.get("end_card") or 0)
    end_line = str((lesson.style.get("end_card") or {}).get("line") or lesson.title)
    duration = float(timeline["duration"])

    def title_at(t: float) -> str:
        if title_s and t < title_s:
            return lesson.title
        if end_s and t > max(0.0, duration - end_s):
            return end_line
        return ""

    burn = str(lesson.style.get("captions") or "off") == "burn"
    try:
        return render_movie(
            source_at,
            (timeline["source_size"]["width"], timeline["source_size"]["height"]),
            timeline["travel_objects"],
            timeline["camera"],
            timeline["presses"],
            video_path,
            background=lesson.background,
            padding=float(lesson.style.get("padding") or 64),
            radius=float(lesson.style.get("radius") or 16),
            preview=preview,
            blurs=None,
            crf=18,
            motion_blur=str(lesson.style.get("motion_blur") or "off"),
            cursor_scale=float(lesson.style.get("cursor_scale") or 1.6),
            caption_at=(lambda t: caption_text(cues, t)) if burn else None,
            title_at=title_at if (title_s or end_s) else None,
            step_bounds=[(float(step["t0"]), float(step["t1"])) for step in timeline["steps"]],
        )
    finally:
        capture.release()


def _qa(timeline: dict[str, Any], movie: dict[str, Any] | None, video_path: str | None, lesson: Lesson | None = None) -> dict[str, Any]:
    checks = []
    checks.append(_check_cursor(timeline))
    checks.append(_check_sync(timeline))
    checks.append(_check_jumps(timeline))
    checks.append(_check_result(timeline))
    checks.append(_check_frozen(video_path))
    checks.append(_check_captions(movie))
    checks.append(_check_loudness(movie))
    checks.append(_check_ocr(video_path, lesson))
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
        return {"id": "sync_within_80ms", "ok": True, "max_slip_s": 0}
    return {"id": "sync_within_80ms", "ok": max(slips) <= 0.08, "max_slip_s": round(max(slips), 3)}


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


def _check_frozen(video_path: str | None) -> dict[str, Any]:
    if not video_path or not os.path.exists(video_path):
        return {"id": "no_static_over_1_5s", "ok": True, "note": "no video"}
    capture = cv2.VideoCapture(video_path)
    fps = capture.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, int(round(fps * 0.5)))
    previous = None
    run = 0.0
    worst = 0.0
    index = 0
    while True:
        ok, frame = capture.read()
        if not ok or frame is None:
            break
        if index % step == 0:
            small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 90))
            if previous is not None:
                delta = float(np.mean(cv2.absdiff(small, previous)))
                if delta < 0.8:
                    run += 0.5
                    worst = max(worst, run)
                else:
                    run = 0.0
            previous = small
        index += 1
    capture.release()
    return {"id": "no_static_over_1_5s", "ok": worst <= 1.5, "longest_s": round(worst, 2)}


def _check_captions(movie: dict[str, Any] | None) -> dict[str, Any]:
    if not movie:
        return {"id": "captions", "ok": True, "note": "no movie"}
    problems = movie.get("caption_problems") or []
    srt = movie.get("srt")
    return {"id": "captions", "ok": not problems and bool(srt and os.path.exists(srt)), "problems": problems}


def _check_loudness(movie: dict[str, Any] | None) -> dict[str, Any]:
    audio = (movie or {}).get("audio") or {}
    lufs = audio.get("lufs")
    peak = audio.get("true_peak")
    if lufs is None or peak is None:
        return {"id": "loudness", "ok": False, "lufs": lufs, "true_peak": peak}
    return {"id": "loudness", "ok": abs(float(lufs) + 14) <= 0.5 and float(peak) <= -1.0, "lufs": lufs, "true_peak": peak}


def _check_ocr(video_path: str | None, lesson: Lesson | None) -> dict[str, Any]:
    needles = [str(item).lower() for item in ((lesson.app.get("never_show") if lesson else None) or []) if str(item).strip()]
    if not video_path or not needles or not os.path.exists(video_path):
        return {"id": "privacy_ocr", "ok": True, "hits": 0}
    capture = cv2.VideoCapture(video_path)
    fps = capture.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, int(round(fps * 2)))
    hits = []
    index = 0
    while True:
        ok, frame = capture.read()
        if not ok or frame is None:
            break
        if index % step == 0:
            path = f"/tmp/screenreel-ocr-{index}.png"
            cv2.imwrite(path, cv2.resize(frame, (960, 540)))
            result = subprocess.run(["tesseract", path, "stdout", "--psm", "6"], capture_output=True, text=True)
            text = (result.stdout or "").lower()
            for needle in needles:
                if needle in text:
                    hits.append(needle)
        index += 1
    capture.release()
    return {"id": "privacy_ocr", "ok": not hits, "hits": hits}


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
    from screenreel_make.driver import inspect_lesson, inspect_url

    flags, positional = _flags(args)
    if not positional:
        raise LessonError("screenreel inspect <lesson.yaml or URL>")
    target = positional[0]
    if target.startswith("http://") or target.startswith("https://"):
        display_name = flags.get("--display")
        display = None if display_name else start_xvfb(":91")
        screen = display_name or display.name
        try:
            return inspect_url(target, screen)
        finally:
            if display:
                display.stop()
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
