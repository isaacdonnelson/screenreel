import json
import os
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from screenreel_make.blur import apply_blurs
from screenreel_make.camera import (
    Box,
    Shot,
    build_camera_track,
    crop_rect,
    group_shots,
    pan_rate,
    reversal_gap,
    shot_for_boxes,
    zoom_rate,
)
from screenreel_make.cursor import Point, control_points, peak_speed, plan_travel, sample_cursor, travel_duration
from screenreel_make.lesson import LessonError, load_lesson
from screenreel_make.marks import append_mark, drain_inbox
from screenreel_make.plan import merge_cuts, resolve_zooms, write_layers
from screenreel_make.render import render_frame, ripple_ages
from screenreel_make.camera import CameraSample
from screenreel_make.voice import (
    cache_key,
    estimate_words,
    prepare_tracks,
    retime,
    schedule,
    words_from_alignment,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "lessons" / "testfiesta-3-1.yaml"
ENV = {
    "TESTFIESTA_URL": "https://practice.example.test",
    "TESTFIESTA_USER": "student@example.test",
    "TESTFIESTA_PASSWORD": "not-a-real-secret",
}


def test_sample_lesson_validates_and_keeps_secrets_out_of_the_file():
    text = SAMPLE.read_text(encoding="utf-8")
    assert "${TESTFIESTA_URL}" in text
    assert "${TESTFIESTA_USER}" in text
    assert "${TESTFIESTA_PASSWORD}" in text
    assert "not-a-real-secret" not in text
    assert "@gmail.com" not in text.split("never_show")[0]
    lesson = load_lesson(str(SAMPLE), environ=ENV)
    assert lesson.lesson == "3-1"
    assert lesson.base_url == "https://practice.example.test"
    assert any(action.at == "click" for action in lesson.steps[0].actions)
    with pytest.raises(LessonError) as missing:
        load_lesson(str(SAMPLE), environ={})
    assert missing.value.code == "env_missing"


def test_missing_anchor_and_em_dash_fail_with_the_step_id(tmp_path):
    broken = tmp_path / "bad.yaml"
    broken.write_text(
        """
lesson: bad
title: Bad
app: {base_url: https://example.test}
steps:
  - id: name
    say: "Name the run."
    do:
      - click: {role: button, name: Save}
        at: "Save"
""",
        encoding="utf-8",
    )
    with pytest.raises(LessonError) as error:
        load_lesson(str(broken))
    assert error.value.code == "anchor_missing"
    assert error.value.step == "name"
    dashed = tmp_path / "dash.yaml"
    dashed.write_text(
        """
lesson: dash
title: Dash
app: {base_url: https://example.test}
steps:
  - id: one
    say: "Wait \\u2014 then click."
    do:
      - hold: 0.2
""",
        encoding="utf-8",
    )
    # The escape above is a literal backslash-u in a plain string, so write the real character.
    dashed.write_text(
        "lesson: dash\ntitle: Dash\napp: {base_url: https://example.test}\nsteps:\n"
        "  - id: one\n    say: \"Wait \u2014 then click.\"\n    do:\n      - hold: 0.2\n",
        encoding="utf-8",
    )
    with pytest.raises(LessonError) as dash_error:
        load_lesson(str(dashed))
    assert dash_error.value.code == "em_dash"
    assert dash_error.value.step == "one"


def test_cli_prints_json_and_exits_nonzero_on_a_bad_lesson(tmp_path):
    broken = tmp_path / "bad.yaml"
    broken.write_text(
        "lesson: bad\ntitle: Bad\napp: {base_url: https://example.test}\nsteps:\n"
        "  - id: one\n    say: Click.\n    do:\n      - click: {role: button, name: Go}\n        at: Missing\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["python3", str(ROOT / "linux" / "screenreel"), "make", str(broken), "--plan-only"],
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["ok"] is False
    assert payload["step"] == "one"


def test_cursor_path_is_eased_and_lands_before_the_press():
    duration = travel_duration(400)
    assert duration == pytest.approx(0.48, abs=0.001)
    assert travel_duration(100) == pytest.approx(0.35, abs=0.001)
    start, end = Point(0, 0), Point(400, 0)
    travel = plan_travel(start, end, press=2.0, index=0)
    assert travel.arrive == pytest.approx(1.88)
    assert travel.press - travel.arrive == pytest.approx(0.12)
    assert sample_cursor([travel], travel.press, start)[0].distance(end) <= 2
    assert peak_speed(travel) < 2400
    c1, _c2, fraction = control_points(start, end, 0)
    assert 0.06 <= fraction <= 0.14
    assert abs(c1.y) == pytest.approx(400 * fraction, rel=0.01)
    other, _, _ = control_points(start, end, 1)
    assert c1.y * other.y < 0
    scale_down = travel.press_scale(travel.press + 0.08)
    assert scale_down == pytest.approx(0.86, abs=0.02)
    assert travel.press_scale(travel.release + 0.14) == pytest.approx(1.0, abs=0.02)
    previous = sample_cursor([travel], travel.t0, start)[0]
    for frame in range(1, int(travel.duration * 60)):
        point = sample_cursor([travel], travel.t0 + frame / 60, start)[0]
        assert previous.distance(point) <= 60
        previous = point


def test_zoom_easing_is_in_output_time_and_a_list_keeps_auto_zooms():
    events = [
        {"t": 0.1, "type": "move", "x": 10, "y": 20},
        {"t": 1.0, "type": "click", "x": 100, "y": 200},
        {"t": 3.0, "type": "key", "x": 0, "y": 0},
        {"t": 3.2, "type": "key", "x": 0, "y": 0},
    ]
    auto = resolve_zooms(events, "auto")
    added = resolve_zooms(events, [{"t": 9, "x": 4, "y": 5, "scale": 1.2, "hold": 0.4, "id": "manual-1"}])
    assert len(added) == len(auto) + 1
    assert any(zoom["id"] == "manual-1" for zoom in added)
    tweaked = resolve_zooms(events, {
        "auto": True,
        "add": [{"t": 9, "x": 4, "y": 5, "id": "manual-1"}],
        "remove": ["auto-click-1"],
        "tweak": {"manual-1": {"scale": 1.33}},
    })
    assert all(zoom["id"] != "auto-click-1" for zoom in tweaked)
    manual = next(zoom for zoom in tweaked if zoom["id"] == "manual-1")
    assert manual["scale"] == pytest.approx(1.33)
    shots = [
        Shot(1.2, 400, 300, 1.6, "click"),
        Shot(2.0, 900, 300, 1.55, "nearby"),
        Shot(6.0, 200, 800, 1.2, "later"),
    ]
    grouped = group_shots(shots)
    assert grouped[1].scale == pytest.approx(grouped[0].scale)
    assert grouped[2].scale == pytest.approx(1.2)
    track = build_camera_track(grouped, duration=4.0, fps=60, source_size=(1920, 1080), max_zoom=1.6)
    assert zoom_rate(track) <= 1.2
    assert pan_rate(track, 1920) <= 0.61
    gap = reversal_gap(track)
    assert gap is None or gap >= 0.5
    # A 6x source ramp must not shorten this ease: the track never reads source time.
    rising = [sample.scale for sample in track if sample.t < 1.2]
    assert max(rising) - min(rising) > 0.2
    assert track[int(0.3 * 60)].scale < 1.6


def test_plan_keeps_overrides_and_is_byte_stable(tmp_path):
    overrides = {
        "cuts": [{"id": "hand-cut", "start": 4.0, "end": 5.0, "manual": True}],
        "zooms": {"auto": True, "add": [{"id": "manual-1", "t": 2, "x": 3, "y": 4, "scale": 1.2, "hold": 0.4}]},
    }
    (tmp_path / "overrides.json").write_text(json.dumps(overrides, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    before = (tmp_path / "overrides.json").read_bytes()
    auto = {
        "cuts": [{"id": "auto-cut-1", "start": 1.0, "end": 2.0}],
        "events": [{"t": 1.0, "type": "click", "x": 10, "y": 10}],
        "zooms": "auto",
        "actions": [],
        "duration": 8,
        "segments": [],
        "style": {},
    }
    first = write_layers(str(tmp_path), auto, existing_cuts=[])
    assert any(cut["id"] == "hand-cut" for cut in first["cuts"])
    assert any(zoom["id"] == "manual-1" for zoom in first["zooms"])
    assert any(zoom["id"] == "auto-click-1" for zoom in first["zooms"])
    auto["cuts"] = [{"id": "auto-cut-1", "start": 1.5, "end": 2.5}]
    second = write_layers(str(tmp_path), auto, existing_cuts=first["cuts"])
    assert any(cut["id"] == "hand-cut" for cut in second["cuts"])
    assert any(cut["id"] == "auto-cut-1" and cut["start"] == 1.5 for cut in second["cuts"])
    assert (tmp_path / "overrides.json").read_bytes() == before
    again = write_layers(str(tmp_path), auto, existing_cuts=second["cuts"])
    assert (tmp_path / "plan.json").read_bytes() == json.dumps(
        json.loads((tmp_path / "plan.json").read_text(encoding="utf-8")),
        indent=2,
        sort_keys=True,
    ).encode() + b"\n"
    assert again["cuts"] == second["cuts"]


def test_blur_uses_a_top_left_origin_and_survives_zoom():
    image = np.zeros((120, 200, 3), np.uint8)
    image[:, :] = (255, 255, 255)
    image[:50, 0:100:2] = (0, 0, 255)
    image[:50, 1:100:2] = (0, 255, 0)
    image[70:, :100] = (255, 0, 0)
    blurred = apply_blurs(image, [{"x": 0, "y": 0, "width": 100, "height": 50, "radius": 8}])
    top = blurred[10, 10]
    assert top[2] > 40 and top[1] > 40
    bottom = blurred[100, 10]
    assert bottom[0] > 200 and bottom[2] < 40
    frame = render_frame(
        image,
        CameraSample(0, 40, 20, 2.0),
        cursor=None,
        cursor_scale=1,
        ripples=[],
        background="#000000",
        padding=0,
        radius=0,
        canvas=(200, 120),
        blurs=[{"x": 0, "y": 0, "width": 100, "height": 50, "radius": 8}],
    )
    zoomed = frame[30, frame.shape[1] // 2]
    assert int(zoomed[2]) > 40 and int(zoomed[1]) > 40


def test_ripple_lasts_24_frames_at_60_fps_inside_a_speed_ramp():
    presses = [{"output": 0.2, "x": 10, "y": 10}]
    visible = [ripple_ages(presses, frame / 60) for frame in range(60)]
    assert sum(1 for item in visible if item) >= 24


def test_voice_anchors_and_cache(tmp_path):
    alignment = {
        "characters": list("Click Next"),
        "character_start_times_seconds": [0.00, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90],
        "character_end_times_seconds": [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.00],
    }
    words = words_from_alignment(alignment)
    assert [word["word"] for word in words] == ["Click", "Next"]
    assert words[1]["start"] == pytest.approx(0.6)
    lesson = load_lesson(str(ROOT / "tests" / "fixtures" / "click.yaml"), environ={"FIXTURE_URL": "https://example.test"})
    calls = {"n": 0}

    def fetch(text, voice_id, model, api_key, opener=None):
        calls["n"] += 1
        return {"words": estimate_words(text), "audio_base64": ""}

    os.environ["ELEVENLABS_API_KEY"] = "test-key"
    try:
        first = prepare_tracks(lesson, str(tmp_path), allow_estimated=False, reuse=True, opener=None)
    finally:
        os.environ.pop("ELEVENLABS_API_KEY", None)
    # No network: estimated provider on the fixture lesson does not call ElevenLabs.
    assert first["tracks"]["next"][1]["word"] == "Next"
    scheduled = schedule(lesson, first["tracks"])
    press = scheduled["actions"][0]
    assert press["id"] == "next.click-1"
    assert abs(press["press"] - (press["anchor_t"] + lesson.lead)) < 1e-6
    retimed = retime(scheduled, [{"id": "next.click-1", "capture": press["press"] + 1.4}], capture_duration=4)
    assert retimed["ok"]
    assert abs(retimed["presses"][0]["slip"]) <= 0.15
    late = retime(scheduled, [{"id": "next.click-1", "capture": 0.1}], capture_duration=0.2)
    # A press with no idle to cut, already at the start, stays within the map.
    assert "presses" in late
    key = cache_key("Click Next.", "voice", "eleven_v3", {})
    assert len(key) == 64


def test_marks_are_timestamped_at_call_time_and_none_are_skipped(tmp_path):
    inbox = tmp_path / "inbox.jsonl"
    started = 1_000.0
    for index in range(100):
        append_mark(str(inbox), f"step {index}", mono=started + 0.01 * (index + 1))
    first, offset = drain_inbox(str(inbox), 0, started, drain_mono=started + 50)
    assert len(first) == 100
    assert first[0]["t"] == pytest.approx(0.01, abs=0.001)
    assert first[-1]["label"] == "step 99"
    # A mark that arrives after the snapshot is not jumped over.
    append_mark(str(inbox), "last", mono=started + 1.25)
    second, _offset = drain_inbox(str(inbox), offset, started, drain_mono=started + 80)
    assert [item["label"] for item in second] == ["last"]
    assert second[0]["t"] == pytest.approx(1.25, abs=0.001)
    # Partial line stays buffered.
    with inbox.open("a", encoding="utf-8") as handle:
        handle.write('{"label":"partial"')
    partial, stuck = drain_inbox(str(inbox), _offset, started, drain_mono=started + 90)
    assert partial == []
    assert stuck == _offset


def test_shot_keeps_the_result_inside_the_frame():
    target = Box(800, 400, 80, 40)
    result = Box(900, 420, 120, 60)
    cx, cy, scale = shot_for_boxes(target, result, (1920, 1080), max_zoom=1.6)
    crop = crop_rect(cx, cy, scale, (1920, 1080))
    assert crop.contains(target.union(result))
    assert scale <= 1.6


def test_scripted_make_clicks_the_fixture_at_60_fps(tmp_path):
    if subprocess.call(["bash", "-lc", "command -v Xvfb && command -v ffmpeg && command -v google-chrome"], stdout=subprocess.DEVNULL) != 0:
        pytest.skip("capture tools are missing")
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from screenreel_make.capture import start_xvfb

    handler = lambda *args, **kwargs: SimpleHTTPRequestHandler(*args, directory=str(ROOT / "tests" / "fixtures"), **kwargs)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    thread.start()
    display = start_xvfb(":96", width=1920, height=1080)
    take = tmp_path / "fixture-take"
    env = {**os.environ, "FIXTURE_URL": f"http://127.0.0.1:{server.server_address[1]}", "DISPLAY": display.name}
    script = str(ROOT / "linux" / "screenreel")
    try:
        completed = subprocess.run(
            [
                "python3", script, "make", str(ROOT / "tests" / "fixtures" / "click.yaml"),
                "--take", str(take),
                "--display", display.name,
                "--preview",
                "--allow-estimated-voice",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        payload = json.loads(completed.stdout)
        assert payload["ok"], completed.stdout + completed.stderr
        session = json.loads((take / "session.json").read_text(encoding="utf-8"))
        assert session["clock"] == "CLOCK_MONOTONIC"
        assert session["fps"] == 60
        assert session["width"] == 1920 and session["height"] == 1080
        assert session["first_frame_mono"] >= session["started_mono"] - 0.05
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate,width,height", "-of", "json", str(take / "raw.mov")],
            capture_output=True,
            text=True,
            check=True,
        )
        stream = json.loads(probe.stdout)["streams"][0]
        assert stream["avg_frame_rate"] in {"60/1", "60/1"}
        assert int(stream["width"]) == 1920 and int(stream["height"]) == 1080
        presses = [json.loads(line) for line in (take / "actions.jsonl").read_text(encoding="utf-8").splitlines()]
        press = next(item for item in presses if item.get("type") == "press")
        box = press["box"]
        assert box["w"] == pytest.approx(180, abs=8)
        assert box["h"] == pytest.approx(56, abs=8)
        assert box["x"] <= press["x"] <= box["x"] + box["w"]
        assert box["y"] <= press["y"] <= box["y"] + box["h"]
        assert payload["qa"]["failed"] == 0
        assert (take / "preview.mp4").exists()
    finally:
        server.shutdown()
        display.stop()


def test_live_recorder_keeps_a_burst_of_marks_including_the_last(tmp_path):
    if subprocess.call(["bash", "-lc", "command -v Xvfb && command -v xinput && command -v ffmpeg"], stdout=subprocess.DEVNULL) != 0:
        pytest.skip("capture tools are missing")
    from screenreel_make.capture import start_xvfb

    display = start_xvfb(":93", width=1280, height=720)
    take = tmp_path / "take"
    env = {**os.environ, "DISPLAY": display.name}
    script = str(ROOT / "linux" / "screenreel")
    try:
        started = subprocess.run(
            ["python3", script, "record", "start", "--take", str(take), "--fps", "10"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert json.loads(started.stdout)["ok"], started.stdout + started.stderr
        session = json.loads((take / "session.json").read_text(encoding="utf-8"))
        origin = session["started_mono"]
        stamps = []
        for index in range(100):
            marked = subprocess.run(
                ["python3", script, "mark", "--take", str(take), "--label", f"step {index}"],
                capture_output=True,
                text=True,
                env=env,
            )
            stamps.append(json.loads(marked.stdout)["mono"])
        stopped = subprocess.run(
            ["python3", script, "record", "stop", "--take", str(take)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert json.loads(stopped.stdout)["ok"], stopped.stdout
        events = [json.loads(line) for line in (take / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        markers = [event for event in events if event["type"] == "marker"]
        assert len(markers) == 100
        assert markers[-1]["label"] == "step 99"
        for stamp, marker in zip(stamps, markers):
            assert abs(marker["t"] - (stamp - origin)) <= 0.05
    finally:
        subprocess.run(["python3", script, "record", "stop", "--take", str(take)], capture_output=True, env=env)
        display.stop()
