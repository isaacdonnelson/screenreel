"""Playwright driver on a dedicated Chrome profile.

The real pointer follows the same eased path the renderer draws, in CSS
pixels. Element boxes are stored in capture pixels so the synthetic cursor
lands on the element, not on a guessed coordinate.
"""

from __future__ import annotations

import os
import time
from typing import Any

from screenreel_make.cursor import Point, plan_travel
from screenreel_make.lesson import ActionSpec, Lesson, LessonError


def profile_dir(name: str, root: str | None = None) -> str:
    base = root or os.path.join(os.getcwd(), ".screenreel", "profiles")
    path = os.path.join(base, name)
    os.makedirs(path, exist_ok=True)
    return path


def chrome_args() -> list[str]:
    return [
        "--kiosk",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-infobars",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-features=TranslateUI",
        "--window-size=1920,1080",
        "--window-position=0,0",
        "--hide-scrollbars",
        "--disable-notifications",
        "--disable-gpu",
        "--ozone-platform=x11",
        "--password-store=basic",
    ]


def launch(playwright: Any, lesson: Lesson, display: str, profile: str | None = None):
    view = lesson.viewport
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=profile or profile_dir(lesson.profile),
        channel="chrome",
        headless=False,
        viewport={"width": int(view["width"]), "height": int(view["height"])},
        device_scale_factor=view["scale"],
        args=chrome_args(),
        ignore_https_errors=True,
        env={**os.environ, "DISPLAY": display},
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.on("framenavigated", lambda frame: _inject(page, lesson, False) if frame == page.main_frame else None)
    return context, page


def _inject(page: Any, lesson: Lesson, keep_toasts: bool) -> None:
    selectors = lesson.hide_selectors
    css = "\n".join(f"{item} {{ display: none !important; }}" for item in selectors)
    if not keep_toasts:
        css += "\n[role=alert], [role=status], .toast { display: none !important; }"
    css += "\n* { scroll-behavior: auto !important; }"
    page.evaluate(
        """(css) => {
            let tag = document.getElementById('screenreel-hide');
            if (!tag) {
                tag = document.createElement('style');
                tag.id = 'screenreel-hide';
                document.documentElement.appendChild(tag);
            }
            tag.textContent = css;
        }""",
        css,
    )


def _origin(page: Any) -> dict[str, float]:
    box = page.evaluate(
        """() => ({
            x: window.screenX || 0,
            y: window.screenY || 0,
            dpr: window.devicePixelRatio || 1,
            w: window.innerWidth,
            h: window.innerHeight
        })"""
    )
    return {
        "x": float(box["x"]),
        "y": float(box["y"]),
        "dpr": float(box["dpr"] or 1),
    }


def screen_box(css_box: dict[str, float], origin: dict[str, float]) -> dict[str, float]:
    dpr = origin["dpr"] or 1
    return {
        "x": origin["x"] + float(css_box["x"]) * dpr,
        "y": origin["y"] + float(css_box["y"]) * dpr,
        "w": float(css_box["width"]) * dpr,
        "h": float(css_box["height"]) * dpr,
    }


def locator_for(page: Any, spec: dict[str, Any]):
    if not spec:
        raise LessonError("Missing target")
    within = spec.get("within")
    root = locator_for(page, within) if isinstance(within, dict) else page
    if spec.get("role") and spec.get("name"):
        loc = root.get_by_role(spec["role"], name=spec["name"])
    elif spec.get("role") and spec.get("has_text"):
        loc = root.get_by_role(spec["role"]).filter(has_text=spec["has_text"])
    elif spec.get("role"):
        loc = root.get_by_role(spec["role"])
    elif spec.get("label"):
        loc = root.get_by_label(spec["label"])
    elif spec.get("text"):
        loc = root.get_by_text(spec["text"])
    elif spec.get("selector"):
        loc = root.locator(spec["selector"])
    else:
        raise LessonError(f"Cannot build a locator from {spec}")
    if spec.get("has_text") and not spec.get("role"):
        loc = loc.filter(has_text=spec["has_text"])
    return loc


def accessible_names(page: Any, limit: int = 10) -> list[str]:
    try:
        names = page.evaluate(
            """(limit) => {
                const nodes = Array.from(document.querySelectorAll('button, a, input, [role], h1, h2, label'));
                return nodes.slice(0, limit).map((el) => {
                    const role = el.getAttribute('role') || el.tagName.toLowerCase();
                    const name = (el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder') || '').trim();
                    return role + ' ' + name;
                });
            }""",
            limit,
        )
    except Exception:
        return []
    return [str(item) for item in names][:limit]


def wait_settled(page: Any, timeout: float = 15.0) -> float:
    """Network idle 300 ms, mutations quiet for 250 ms, no running CSS animations."""
    deadline = time.monotonic() + timeout
    try:
        page.wait_for_load_state("networkidle", timeout=min(3000, int(timeout * 1000)))
    except Exception:
        pass
    page.evaluate(
        """() => {
            if (window.__srObserver) return;
            window.__srMut = performance.now();
            window.__srObserver = new MutationObserver(() => { window.__srMut = performance.now(); });
            window.__srObserver.observe(document.documentElement, {subtree: true, childList: true, attributes: true});
        }"""
    )
    while time.monotonic() < deadline:
        quiet = page.evaluate(
            """() => {
                const mut = (performance.now() - (window.__srMut || 0)) > 250;
                const anim = (document.getAnimations ? document.getAnimations() : []).every((item) => item.playState !== 'running');
                return mut && anim;
            }"""
        )
        if quiet:
            return time.monotonic()
        page.wait_for_timeout(50)
    return time.monotonic()


def key_delays(text: str, finish_in: float | None = None, seed: int = 1) -> list[float]:
    """Per-key delays in seconds. 45-80 ms, plus or minus 20%, extra gap on spaces."""
    import random

    rng = random.Random(seed)
    long_text = len(text) > 40
    low, high = (0.030, 0.050) if long_text else (0.045, 0.080)
    delays = []
    for char in text:
        base = rng.uniform(low, high)
        jitter = base * rng.uniform(-0.20, 0.20)
        delay = max(0.02, base + jitter)
        if char == " ":
            delay += rng.uniform(0.06, 0.12)
        delays.append(delay)
    if finish_in and delays and sum(delays) > finish_in:
        scale = finish_in / sum(delays)
        delays = [max(0.03 if not long_text else 0.02, item * scale) for item in delays]
    return delays


def run_lesson(
    page: Any,
    lesson: Lesson,
    planned: list[dict[str, Any]],
    clock_origin: float,
    actions_path: str,
) -> list[dict[str, Any]]:
    import json

    events: list[dict[str, Any]] = []
    handle = open(actions_path, "w", encoding="utf-8")
    cursor = Point(960, 540)
    travel_index = 0
    used: set[str] = set()

    def write(payload: dict[str, Any]) -> None:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
        handle.flush()
        events.append(payload)

    try:
        _inject(page, lesson, False)
        for hook in lesson.setup:
            if "goto" in hook:
                _goto(page, lesson, str(hook["goto"]))
            elif "script" in hook:
                write({
                    "t": round(time.monotonic() - clock_origin, 3),
                    "type": "warning",
                    "code": "setup_hook_not_run",
                    "message": f"Setup script {hook.get('script')} is not built in yet",
                })
        by_id = {item["id"]: item for item in planned}
        for step in lesson.steps:
            _inject(page, lesson, step.keep_toasts)
            for action in step.actions:
                ident = _match_id(step.id, action, by_id, used)
                plan = by_id.get(ident) if ident else None
                if action.kind == "goto":
                    _goto(page, lesson, action.text or "")
                    settled = wait_settled(page)
                    write({"t": round(settled - clock_origin, 3), "type": "goto", "id": ident, "url": page.url, "step": step.id})
                    continue
                if action.kind == "scroll_into_view":
                    loc = _resolve(page, action.spec, step.id)
                    loc.scroll_into_view_if_needed(timeout=8000)
                    continue
                if action.kind == "wait_for":
                    _wait_for(page, action.wait_for or action.spec, step.id)
                    continue
                if action.kind == "hold":
                    time.sleep(float(action.seconds or 0))
                    continue
                if action.kind == "script":
                    write({
                        "t": round(time.monotonic() - clock_origin, 3),
                        "type": "warning",
                        "code": "setup_hook_not_run",
                        "step": step.id,
                        "message": f"Script {action.text} is not built in yet",
                    })
                    continue
                if action.kind in {"click", "select", "type"}:
                    cursor, travel_index = _perform(
                        page, lesson, step.id, action, plan, cursor, travel_index, clock_origin, write
                    )
                    continue
                raise LessonError(f"Unknown action {action.kind}", step=step.id)
    finally:
        handle.close()
    return events


def _match_id(step_id: str, action: ActionSpec, planned: dict[str, dict[str, Any]], used: set[str]) -> str | None:
    prefix = f"{step_id}.{action.kind}-"
    matches = sorted(ident for ident in planned if ident.startswith(prefix))
    for ident in matches:
        if ident in used:
            continue
        if action.at and planned[ident].get("at") not in {None, action.at}:
            continue
        used.add(ident)
        return ident
    return None


def _goto(page: Any, lesson: Lesson, target: str) -> None:
    if target.startswith("http://") or target.startswith("https://") or target.startswith("file:"):
        url = target
    else:
        url = lesson.base_url.rstrip("/") + "/" + target.lstrip("/")
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    _inject(page, lesson, False)


def _resolve(page: Any, spec: dict[str, Any], step: str):
    try:
        loc = locator_for(page, spec)
        loc.wait_for(state="visible", timeout=8000)
        return loc
    except LessonError:
        raise
    except Exception as error:
        names = accessible_names(page)
        shot = ""
        try:
            shot = os.path.join("/tmp", f"screenreel-{step}.png")
            page.screenshot(path=shot)
        except Exception:
            shot = ""
        raise LessonError(
            f"Selector not found ({error}). Nearest names: {', '.join(names)}",
            step=step,
            code="selector_not_found",
        ) from error


def _wait_for(page: Any, spec: dict[str, Any] | None, step: str) -> None:
    if not spec:
        return
    try:
        if spec.get("text"):
            page.get_by_text(spec["text"]).first.wait_for(state="visible", timeout=8000)
        elif spec.get("role"):
            locator_for(page, spec).first.wait_for(state="visible", timeout=8000)
    except Exception as error:
        raise LessonError(f"wait_for failed: {error}", step=step, code="wait_for") from error


def _perform(page, lesson, step, action, plan, cursor, travel_index, clock_origin, write):
    if action.kind == "type" and not action.spec:
        loc = page.locator(":focus")
    else:
        loc = _resolve(page, action.spec, step)
    origin = _origin(page)
    css = loc.bounding_box()
    if not css:
        raise LessonError("Target has no box", step=step, code="selector_not_found")
    box = screen_box(css, origin)
    center = Point(box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
    press_output = float(plan["press"]) if plan else 0.0
    travel = plan_travel(cursor, center, press_output, travel_index)
    _wait_until(clock_origin, travel.t0)
    _move_along(page, travel, origin, clock_origin)
    if action.kind in {"click", "select"}:
        _wait_until(clock_origin, press_output)
        page.mouse.down()
        pressed = time.monotonic()
        time.sleep(0.08)
        page.mouse.up()
        released = time.monotonic()
        if action.wait_for:
            _wait_for(page, action.wait_for, step)
        settled = wait_settled(page)
        result = _result_box(page, action.result, origin) if action.result else None
        write({
            "t": round(pressed - clock_origin, 3),
            "type": "press",
            "id": plan["id"] if plan else None,
            "step": step,
            "x": round(center.x, 2),
            "y": round(center.y, 2),
            "box": {key: round(box[key], 2) for key in ("x", "y", "w", "h")},
            "release": round(released - clock_origin, 3),
            "settled_at": round(settled - clock_origin, 3),
            "result": result,
            "url": page.url,
        })
    elif action.kind == "type":
        _wait_until(clock_origin, press_output)
        text = action.text or ""
        delays = key_delays(text, seed=travel_index + 1)
        if action.paste:
            page.keyboard.insert_text(text)
        else:
            for char, delay in zip(text, delays):
                page.keyboard.type(char)
                time.sleep(delay)
        expected = action.expect_value if action.expect_value is not None else text
        value = ""
        try:
            value = loc.input_value(timeout=2000)
        except Exception:
            value = page.evaluate("() => document.activeElement && document.activeElement.value || ''")
        if expected and value != expected:
            try:
                loc.fill("")
            except Exception:
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
            for char, delay in zip(text, delays):
                page.keyboard.type(char)
                time.sleep(delay)
            try:
                value = loc.input_value(timeout=2000)
            except Exception:
                value = page.evaluate("() => document.activeElement && document.activeElement.value || ''")
            if value != expected:
                raise LessonError(
                    f"Typed value {value!r} != {expected!r}",
                    step=step,
                    code="type_mismatch",
                )
        write({
            "t": round(time.monotonic() - clock_origin, 3),
            "type": "type",
            "id": plan["id"] if plan else None,
            "step": step,
            "text": text,
            "x": round(center.x, 2),
            "y": round(center.y, 2),
            "box": {key: round(box[key], 2) for key in ("x", "y", "w", "h")},
            "keys": len(text),
        })
    return center, travel_index + 1


def _result_box(page: Any, spec: dict[str, Any], origin: dict[str, float]) -> dict[str, float] | None:
    try:
        loc = locator_for(page, spec)
        loc.first.wait_for(state="visible", timeout=3000)
        css = loc.first.bounding_box()
    except Exception:
        return None
    if not css:
        return None
    box = screen_box(css, origin)
    return {key: round(box[key], 2) for key in ("x", "y", "w", "h")}


def _wait_until(origin: float, output_t: float) -> None:
    target = origin + output_t
    delay = target - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def _move_along(page: Any, travel, origin: dict[str, float], clock_origin: float) -> None:
    steps = max(2, int(travel.duration / 0.016))
    dpr = origin["dpr"] or 1
    for index in range(steps + 1):
        t = travel.t0 + travel.duration * index / steps
        _wait_until(clock_origin, t)
        point = travel.position(min(t, travel.arrive))
        css_x = (point.x - origin["x"]) / dpr
        css_y = (point.y - origin["y"]) / dpr
        page.mouse.move(css_x, css_y)


def inspect_lesson(lesson: Lesson, display: str, only: str | None = None) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    shots = []
    unresolved = []
    with sync_playwright() as playwright:
        context, page = launch(playwright, lesson, display)
        try:
            _goto(page, lesson, "/")
            for step in lesson.steps:
                if only and step.id != only:
                    continue
                for action in step.actions:
                    if action.kind == "goto" and action.text:
                        _goto(page, lesson, action.text)
                        continue
                    if action.kind not in {"click", "type", "select", "scroll_into_view"}:
                        continue
                    spec = action.spec
                    if action.kind == "type" and not spec:
                        continue
                    try:
                        loc = locator_for(page, spec)
                        loc.first.wait_for(state="visible", timeout=8000)
                        css = loc.first.bounding_box()
                        name = ""
                        try:
                            name = loc.first.inner_text(timeout=1000)
                        except Exception:
                            name = ""
                        origin = _origin(page)
                        shots.append({
                            "step": step.id,
                            "kind": action.kind,
                            "name": name.strip(),
                            "box": screen_box(css, origin) if css else None,
                        })
                    except Exception as error:
                        unresolved.append({
                            "step": step.id,
                            "spec": spec,
                            "error": str(error),
                            "nearest": accessible_names(page),
                        })
            picture = os.path.join("/tmp", f"screenreel-inspect-{lesson.lesson}.png")
            page.screenshot(path=picture)
        finally:
            context.close()
    return {
        "ok": not unresolved,
        "lesson": lesson.lesson,
        "resolved": shots,
        "unresolved": unresolved,
        "screenshot": picture,
    }
