"""Lesson schema, environment substitution, and narration lint."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
EM_DASH = "\u2014"
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?")


class LessonError(Exception):
    def __init__(self, message: str, step: str | None = None, code: str = "lesson_invalid"):
        super().__init__(message)
        self.message = message
        self.step = step
        self.code = code

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"ok": False, "error": self.message, "code": self.code}
        if self.step:
            body["step"] = self.step
        return body


@dataclass
class ActionSpec:
    kind: str
    spec: dict[str, Any] = field(default_factory=dict)
    at: str | None = None
    result: dict[str, Any] | None = None
    wait_for: dict[str, Any] | None = None
    expect_value: str | None = None
    paste: bool = False
    seconds: float | None = None
    text: str | None = None


@dataclass
class StepSpec:
    id: str
    label: str
    say: str
    actions: list[ActionSpec] = field(default_factory=list)
    keep_toasts: bool = False
    note: str | None = None


@dataclass
class Lesson:
    lesson: str
    title: str
    output: str
    raw: dict[str, Any]
    app: dict[str, Any]
    style: dict[str, Any]
    voice: dict[str, Any]
    audio: dict[str, Any]
    steps: list[StepSpec]
    intro: str | None = None
    setup: list[dict[str, Any]] = field(default_factory=list)
    teardown: list[dict[str, Any]] = field(default_factory=list)
    path: str = ""

    @property
    def base_url(self) -> str:
        return str(self.app.get("base_url") or "")

    @property
    def profile(self) -> str:
        return str(self.app.get("profile") or "default")

    @property
    def viewport(self) -> dict[str, float]:
        view = self.app.get("viewport") or {}
        return {
            "width": float(view.get("width") or 1440),
            "height": float(view.get("height") or 810),
            "scale": float(view.get("scale") or 1.333),
        }

    @property
    def hide_selectors(self) -> list[str]:
        defaults = ["#intercom-container", ".intercom-lightweight-app", ".intercom-launcher"]
        extra = [str(item) for item in (self.app.get("hide") or [])]
        seen: list[str] = []
        for item in defaults + extra:
            if item not in seen:
                seen.append(item)
        return seen

    @property
    def lead(self) -> float:
        return float(self.voice.get("lead") if self.voice.get("lead") is not None else -0.08)

    @property
    def max_zoom(self) -> float:
        return float(self.style.get("max_zoom") or 1.6)

    @property
    def background(self) -> str:
        value = self.style.get("background") or "#1E1B4B"
        if isinstance(value, str):
            return value
        return "#1E1B4B"


def words_in(text: str) -> list[str]:
    return WORD_RE.findall(text or "")


def resolve_env(value: Any, environ: dict[str, str] | None = None, missing: list[str] | None = None) -> Any:
    """Replace ${NAME} placeholders. Empty or absent variables are recorded in missing."""
    env = os.environ if environ is None else environ
    found = missing if missing is not None else []
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            current = env.get(key, "")
            if current == "":
                if key not in found:
                    found.append(key)
                return match.group(0)
            return current

        return ENV_RE.sub(repl, value)
    if isinstance(value, list):
        return [resolve_env(item, env, found) for item in value]
    if isinstance(value, dict):
        return {key: resolve_env(item, env, found) for key, item in value.items()}
    return value


def load_lesson(
    path: str,
    environ: dict[str, str] | None = None,
    require_env: bool = True,
) -> Lesson:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise LessonError("Lesson file must be a YAML mapping")
    missing: list[str] = []
    resolved = resolve_env(raw, environ, missing)
    if require_env and missing:
        names = ", ".join(missing)
        raise LessonError(
            f"Missing environment variable {names}. Set it in the environment; do not write secrets into the lesson.",
            code="env_missing",
        )
    lesson = _parse(resolved, path)
    lint_lesson(lesson)
    return lesson


def lint_lesson(lesson: Lesson) -> None:
    _reject_emdash(lesson.title, None, "title")
    if lesson.intro:
        _reject_emdash(lesson.intro, "intro", "intro")
    end = (lesson.style.get("end_card") or {}).get("line")
    if isinstance(end, str):
        _reject_emdash(end, None, "end card")
    seen: set[str] = set()
    for step in lesson.steps:
        if step.id in seen:
            raise LessonError(f"Duplicate step id {step.id}", step=step.id, code="duplicate_step")
        seen.add(step.id)
        _reject_emdash(step.say, step.id, "narration")
        _reject_emdash(step.label, step.id, "step label")
        spoken = set(words_in(step.say))
        for index, action in enumerate(step.actions, start=1):
            if action.at and action.at not in spoken:
                raise LessonError(
                    f"Anchor \"{action.at}\" is not a word in this step's narration",
                    step=step.id,
                    code="anchor_missing",
                )
            if action.kind == "type" and not action.text:
                raise LessonError("type needs text", step=step.id, code="type_missing")
            if action.kind not in {
                "goto", "click", "type", "select", "scroll_into_view", "wait_for", "hold", "script",
            }:
                raise LessonError(f"Unknown action {action.kind}", step=step.id, code="unknown_action")
            if index and action.kind == "click" and not action.spec:
                raise LessonError("click needs a target", step=step.id, code="click_missing")


def _reject_emdash(text: str, step: str | None, where: str) -> None:
    if EM_DASH in (text or ""):
        raise LessonError(
            f"Em dash is not allowed in {where}",
            step=step,
            code="em_dash",
        )


def _parse(raw: dict[str, Any], path: str) -> Lesson:
    if not raw.get("lesson"):
        raise LessonError("lesson id is required")
    steps = []
    for entry in raw.get("steps") or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            raise LessonError("Each step needs an id")
        steps.append(
            StepSpec(
                id=str(entry["id"]),
                label=str(entry.get("label") or entry["id"]),
                say=str(entry.get("say") or ""),
                actions=_parse_actions(entry.get("do") or []),
                keep_toasts=bool(entry.get("keep_toasts")),
                note=entry.get("note"),
            )
        )
    if not steps:
        raise LessonError("A lesson needs at least one step")
    intro = raw.get("intro") or {}
    intro_say = intro.get("say") if isinstance(intro, dict) else None
    return Lesson(
        lesson=str(raw["lesson"]),
        title=str(raw.get("title") or raw["lesson"]),
        output=str(raw.get("output") or ""),
        raw=raw,
        app=dict(raw.get("app") or {}),
        style=dict(raw.get("style") or {}),
        voice=dict(raw.get("voice") or {}),
        audio=dict(raw.get("audio") or {}),
        steps=steps,
        intro=str(intro_say) if intro_say else None,
        setup=list(raw.get("setup") or []),
        teardown=list(raw.get("teardown") or []),
        path=path,
    )


_META = {"at", "result", "wait_for", "expect_value", "paste", "note"}


def _parse_actions(entries: list[Any]) -> list[ActionSpec]:
    actions: list[ActionSpec] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise LessonError("Each action must be a mapping")
        action_keys = [key for key in entry if key not in _META]
        if not action_keys:
            if not actions:
                raise LessonError("at, result, and wait_for must follow an action")
            _apply_meta(actions[-1], entry)
            continue
        if len(action_keys) != 1:
            raise LessonError("Each action has one verb, such as click or type")
        action = _action_from(action_keys[0], entry[action_keys[0]])
        _apply_meta(action, entry)
        actions.append(action)
    return actions


def _apply_meta(action: ActionSpec, entry: dict[str, Any]) -> None:
    if "at" in entry:
        action.at = str(entry["at"])
    if "result" in entry:
        value = entry["result"]
        action.result = dict(value) if isinstance(value, dict) else {"text": value}
    if "wait_for" in entry:
        value = entry["wait_for"]
        action.wait_for = dict(value) if isinstance(value, dict) else {"text": value}
    if "expect_value" in entry:
        action.expect_value = str(entry["expect_value"])
    if "paste" in entry:
        action.paste = bool(entry["paste"])


def _action_from(kind: str, value: Any) -> ActionSpec:
    if kind == "type":
        return ActionSpec(kind="type", text=str(value), spec={})
    if kind == "hold":
        return ActionSpec(kind="hold", seconds=float(value), spec={})
    if kind == "goto":
        return ActionSpec(kind="goto", text=str(value), spec={})
    if kind == "script":
        return ActionSpec(kind="script", text=str(value), spec={})
    if kind == "wait_for":
        spec = dict(value) if isinstance(value, dict) else {"text": value}
        return ActionSpec(kind="wait_for", wait_for=spec, spec=spec)
    if isinstance(value, dict):
        return ActionSpec(kind=kind, spec=dict(value))
    return ActionSpec(kind=kind, spec={"text": value})
