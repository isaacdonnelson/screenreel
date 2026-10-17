"""Auto plan, overrides, and zoom merge.

auto.json is regenerated. overrides.json is never written. The effective
plan is the merge, so a later plan run keeps hand-added cuts.
"""

from __future__ import annotations

import json
import os
from typing import Any


def cut_key(cut: dict[str, Any]) -> str:
    ident = cut.get("id")
    if ident:
        return f"id:{ident}"
    return "span:{:.3f}:{:.3f}".format(float(cut["start"]), float(cut["end"]))


def merge_cuts(
    new_auto: list[dict[str, Any]],
    previous_auto: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    overrides: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    previous = {cut_key(cut) for cut in previous_auto}
    auto_keys = {cut_key(cut) for cut in new_auto}
    manual: list[dict[str, Any]] = []
    for cut in existing:
        key = cut_key(cut)
        if key in previous or key in auto_keys:
            continue
        ident = str(cut.get("id") or "")
        if ident.startswith("auto-") and not cut.get("manual"):
            continue
        manual.append(cut)
    merged = [dict(cut) for cut in new_auto]
    seen = {cut_key(cut) for cut in merged}
    for cut in manual + overrides:
        key = cut_key(cut)
        if key in seen:
            continue
        merged.append(dict(cut))
        seen.add(key)
    merged.sort(key=lambda cut: (float(cut["start"]), float(cut["end"]), str(cut.get("id") or "")))
    return merged


def auto_zooms(events: list[dict[str, Any]], zoom_scale: float = 1.45, zoom_hold: float = 0.6) -> list[dict[str, Any]]:
    """Same grouping rules as the Mac renderer, with stable ids."""
    zooms: list[dict[str, Any]] = []
    cursor = (0.0, 0.0)
    burst_start: float | None = None
    burst_cursor = (0.0, 0.0)
    last_key: float | None = None
    click_n = 0
    key_n = 0

    def recently(time: float) -> bool:
        return bool(zooms) and time - float(zooms[-1]["t"]) < 0.2

    def close_burst() -> None:
        nonlocal burst_start, last_key, key_n
        if burst_start is None:
            return
        key_n += 1
        zooms.append({
            "id": f"auto-key-{key_n}",
            "t": burst_start,
            "x": burst_cursor[0],
            "y": burst_cursor[1],
            "scale": zoom_scale,
            "hold": zoom_hold,
        })
        burst_start = None
        last_key = None

    for event in sorted(events, key=lambda item: float(item.get("t") or 0)):
        kind = event.get("type")
        time = float(event.get("t") or 0)
        if kind == "move":
            cursor = (float(event.get("x") or 0), float(event.get("y") or 0))
        elif kind in {"click", "down"}:
            if burst_start is not None and time - burst_start > 0.15:
                close_burst()
            else:
                burst_start = None
                last_key = None
            cursor = (float(event.get("x") or 0), float(event.get("y") or 0))
            if not recently(time):
                click_n += 1
                zooms.append({
                    "id": f"auto-click-{click_n}",
                    "t": time,
                    "x": cursor[0],
                    "y": cursor[1],
                    "scale": zoom_scale,
                    "hold": zoom_hold,
                })
        elif kind in {"key", "scroll"}:
            if last_key is not None and burst_start is not None and time - last_key <= 0.4:
                last_key = time
            else:
                if burst_start is not None:
                    close_burst()
                if recently(time):
                    burst_start = None
                    last_key = None
                else:
                    burst_start = time
                    burst_cursor = cursor
                    last_key = time
    close_burst()
    return zooms


def resolve_zooms(events: list[dict[str, Any]], zooms_field: Any, style: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """A zoom list or camera.add never turns auto zooms off."""
    style = style or {}
    scale = float(style.get("zoomScale") or style.get("max_zoom") or 1.45)
    hold = float(style.get("zoomHold") or 0.6)
    generated = auto_zooms(events, zoom_scale=scale, zoom_hold=hold)
    if zooms_field in (None, "auto"):
        return generated
    if isinstance(zooms_field, list):
        spec = {"auto": True, "add": zooms_field, "remove": [], "tweak": {}}
    elif isinstance(zooms_field, dict):
        spec = zooms_field
    else:
        raise ValueError('zooms must be "auto", a list, or {auto, add, remove, tweak}')
    result = list(generated) if spec.get("auto", True) else []
    remove = set(spec.get("remove") or [])
    result = [zoom for zoom in result if zoom.get("id") not in remove]
    for index, extra in enumerate(spec.get("add") or [], start=1):
        zoom = {
            "id": extra.get("id") or f"add-{index}",
            "t": float(extra["t"]),
            "x": float(extra["x"]),
            "y": float(extra["y"]),
            "scale": float(extra.get("scale") or scale),
            "hold": float(extra.get("hold") if extra.get("hold") is not None else hold),
        }
        result.append(zoom)
    tweaks = spec.get("tweak") or {}
    for ident in sorted(tweaks):
        tweak = tweaks[ident]
        for zoom in result:
            if zoom.get("id") != ident:
                continue
            for key in ("t", "x", "y", "scale", "hold"):
                if key in tweak and tweak[key] is not None:
                    zoom[key] = float(tweak[key])
    return result


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def read_json(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def write_layers(
    take: str,
    auto_payload: dict[str, Any],
    existing_cuts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Regenerate auto.json, merge overrides.json, write plan.json.

    overrides.json is only read. A missing file is an empty override set.
    """
    os.makedirs(take, exist_ok=True)
    auto_path = os.path.join(take, "auto.json")
    override_path = os.path.join(take, "overrides.json")
    plan_path = os.path.join(take, "plan.json")
    previous = read_json(auto_path)
    overrides = read_json(override_path)
    merged_cuts = merge_cuts(
        list(auto_payload.get("cuts") or []),
        list(previous.get("cuts") or []),
        list(existing_cuts if existing_cuts is not None else (read_json(os.path.join(take, "edit.json")).get("cuts") or [])),
        list(overrides.get("cuts") or []),
    )
    zoom_overrides = overrides.get("zooms")
    events = list(auto_payload.get("events") or [])
    zooms = resolve_zooms(events, zoom_overrides if zoom_overrides is not None else auto_payload.get("zooms", "auto"), auto_payload.get("style"))
    # Camera adds from the lesson sit on top of auto shots. They are part of
    # the generated auto layer; overrides.json can still remove or tweak them.
    plan = {
        "version": 1,
        "cuts": merged_cuts,
        "segments": auto_payload.get("segments") or [],
        "zooms": zooms,
        "shots": auto_payload.get("shots") or [],
        "actions": auto_payload.get("actions") or [],
        "duration": auto_payload.get("duration") or 0,
        "warnings": auto_payload.get("warnings") or [],
    }
    auto_out = dict(auto_payload)
    auto_out["cuts"] = list(auto_payload.get("cuts") or [])
    auto_out["zooms"] = resolve_zooms(events, auto_payload.get("zooms", "auto"), auto_payload.get("style"))
    with open(auto_path, "w", encoding="utf-8") as handle:
        handle.write(dumps(auto_out))
    with open(plan_path, "w", encoding="utf-8") as handle:
        handle.write(dumps(plan))
    return plan
