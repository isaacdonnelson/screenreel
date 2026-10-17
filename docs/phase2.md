# screenreel Phase 2: from working prototype to promptable, beautiful AI tutorials

Repo: isaacdonnelson/screenreel. Continue on PR #24 (branch cursor/scripted-make-p0-c9d4), or stack a new branch on it. Read docs/screenreel-plan.md (PR #2), epic #23 and issues #8-#22 first.

## Goal
One command, `screenreel make lesson.yaml`, run by an AI agent on a Linux box (DISPLAY :38, 1920x1200, or a private Xvfb display), produces a finished 1920x1080 tutorial: Cursorful/Screen Studio-grade motion, narration in sync, music, SFX and captions, with automatic QA. No human editing, no Mac required.

## Field test results (box, 2026-10-16)
- make drove the real TestFiesta 3-1 flow end to end and captured at 60 fps. The driver works.
- The render is far too slow: a Python per-frame loop piping bgr24 into ffmpeg took more than 10 minutes for about 90 s of video. Target: under 2 min for a 90 s lesson on the box CPU (no GPU).
- Live ElevenLabs speech, the audio mix, captions and most of QA are not implemented.

## Work items (in order)

### 1. Fast renderer (P0, #12)
- Replace the per-pixel Python loop. Options, best first: (a) build one ffmpeg filter graph per segment (zoompan or crop+scale with expressions from precomputed keyframes, plus an overlay of a pre-rendered cursor sprite with eased x/y expressions, rounded corners via alphamerge, and the background and shadow); (b) numpy-vectorized frames with a multiprocessing pool writing chunks, then ffmpeg concat; (c) a GPU-free compositor such as PyAV or Skia. Benchmark and pick the fastest that keeps quality.
- Acceptance: a 90 s lesson renders in 2 min or less at 1920x1080, 60 fps, crf 18; `--preview` (960x540, 30 fps) in 30 s or less. Print progress (percent and ETA) to stderr.

### 2. Voice-first timing with ElevenLabs (P0, #11)
- Read ELEVENLABS_API_KEY from env. Call text-to-speech with timestamps (character alignment) for each step's narration line, using voice and model from lesson.yaml (default voice 7EzWGsX10sAS4c9m9cPf "Jack John", model eleven_v3, highest output format the plan allows, e.g. mp3_44100_192 or pcm_44100). Cache by hash of text, voice and model.
- Also accept prerecorded clips per step (`audio: path`) and use speech-to-text or forced alignment for their word timings.
- Each action can anchor to a word (`at: "Passed"`): the scheduler lands the click on that word's start, minus the cursor travel time. Actions without an anchor are spaced evenly through the line. Idle time is compressed; never freeze longer than 0.8 s unless the step says `hold`.
- Acceptance: every anchored click lands within ±80 ms of its word, and no voice clip overruns its step.

### 3. Built-in audio mix and captions (P0, #13)
- Mix the voice, a music bed (lesson.yaml `music:`, looped), and click and typing SFX (bundled, royalty free). Sidechain-duck the music about 14 dB under the voice with a 150 ms attack and a 400 ms release. Two-pass loudnorm to -14 LUFS integrated, true peak at most -1.0 dBTP.
- Burned-in captions from the alignment (optional flag), using the brand font (Inter, with Liberation Sans as the fallback), at most 2 lines and 42 characters per line, with no em dashes.
- Optional title and end cards from lesson.yaml (logo path, title, colors).
- Acceptance: ebur128 reads -14 ±0.5 LUFS and TP at most -1.0.

### 4. Motion quality (P0/P1, #12 #15 #19)
- Cursor: a macOS-style arrow sprite with shadow at 1.6x scale. Eased bezier paths with a slight arc, 0.35-0.9 s depending on distance. The tip sits exactly on the click point. Hide it after 2 s idle and fade it back in.
- Click: ripple plus a subtle press scale (0.9x) for 120 ms.
- Camera: spring or ease-in-out zoom of 1.4-1.8x to the action region, which follows the typing caret. Hold while the result appears. Zoom out on a page change. Ease durations of at least 0.6 s on the output clock. Never crop the element that changed.
- Typing: real per-key typing at about 70 ms per key (plus jitter), driven by Playwright, with keystroke SFX.
- Step transitions: a 6-frame crossfade or a camera move, never a hard jump.
- Motion blur by 4-subframe supersampling during fast camera or cursor moves (P1).
- Frame: background color or gradient from lesson.yaml (TestFiesta: #1E1B4B, radius 16, padding 64, soft shadow).

### 5. Privacy and cleanliness (P1, #17)
- `mask: [selector, ...]` blurs elements tracked per frame from their DOM boxes. `replace_text` swaps real names and emails in the DOM before capture.
- Inject CSS to hide Intercom, chat widgets and toasts by a configurable selector list (default includes Intercom). Run in a dedicated profile only, with no other tabs.

### 6. Automatic QA and agent contract (P0, #14)
- `make` returns one JSON object: ok, out path, preview path, duration, fps, LUFS, TP, per-step t0/t1, warnings, and a `qa` block. The QA checks: frozen stretches over 1.5 s (fail), cursor-on-target at every click, zoom target in frame, unmasked email or name regex via OCR (tesseract), off-domain frames, captions present, and loudness. Write `contact.jpg` (stills every 5 s plus one per step) for the agent to review.
- `--only step-4` re-renders one step quickly. Overrides merge; nothing gets clobbered.

### 7. Lesson DX (P1, #21)
- A `lesson.yaml` schema with lint: steps of goto, click (selector or text), type, select, check, wait_for, scroll, hover and narrate, plus anchors. Setup and teardown hooks (login, seed data, delete created run). `screenreel inspect URL` lists clickable elements with stable selectors to help the agent write lessons.
- Ship a real, working `lessons/testfiesta-3-1.yaml`. Its selectors were already verified in takes/3-1-live-full on the box; take the box copy from the test run if provided, or verify against https://app.testfiesta.com.

### 8. Docs
- Update SKILL.md and README with the one-command agent workflow, env vars, and troubleshooting.

## Constraints
- Linux first. Keep the Mac Swift renderer compiling, but it's optional. Never commit secrets; use env vars only. Never publish anything. Don't merge; open or update PRs that reference issues. Add tests (unit tests for the scheduler, easing, mix and QA; a fixture end-to-end test on a local HTML page that checks fps, sync within ±80 ms, LUFS and no frozen stretch over 1.5 s).
- No em dashes in any user-facing text or captions.

## Definition of done
On the agent's box: `screenreel make lessons/testfiesta-3-1.yaml` with ELEVENLABS_API_KEY and TESTFIESTA_* set produces a 1920x1080 60 fps out.mp4 in under 5 min wall time end to end, with synced Jack John narration, ducked music, SFX, optional captions, single smooth cursor, eased zooms, no frozen stretch over 1.5 s, -14 LUFS, privacy masks, and a JSON result with qa ok. Report what's done and what's left in the PR.

## What shipped on this branch

The renderer keeps `warpAffine` float transforms. ffmpeg `zoompan` shimmers, and a crop expression with `t` failed to evaluate, so the fast path is a cached background plate, an interior copy, a border blend, and raw frames streamed to libx264. A writer thread overlaps encode with the next frame, because a 1080p frame is larger than the pipe buffer. Progress (`render N% eta Ss`) goes to stderr.

Measured on a 4-core VM, no GPU, crf 18, preset veryfast, `motion_blur: cursor`:

| Clip | Wall time | Extrapolated 90 s |
| --- | --- | --- |
| 8 s, 1920x1080, 60 fps, numpy source | 8.6 s | 97 s |
| Same, reading the previous h264 as the source | 10.0 s | 112 s |
| 8 s preview, 960x540, 30 fps, h264 source | 2.1 s | 23 s |

`motion_blur: cursor` stamps the arrow at four sub-frame positions. `motion_blur: all` supersamples the whole frame on fast camera moves.

Press scale stays at the values locked by the #12 tests: 0.86 for 80 ms, back over 140 ms, arriving 0.12 s before the click. The prompt text said 0.9 for 120 ms.

The music duck is a 200 Hz envelope (150 ms attack, 400 ms release, about 14 dB), then two-pass loudnorm and a trim so ebur128 lands on -14 LUFS ±0.5 with true peak at most -1.0 dBTP. Click SFX is -24 dB and key SFX is -32 dB. An empty `music:` path uses a generated pad.

`--only <step>` writes `steps/<id>.mp4` and leaves `out.mp4` untouched. OCR samples every 2 s. This VM has no ElevenLabs key: tests mock the API, and a step can set `audio: path` with an optional `<path>.words.json` sidecar.
