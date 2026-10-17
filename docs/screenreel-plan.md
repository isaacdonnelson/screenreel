# screenreel: requirements and plan

**Goal (Isaac Donnelson):** "promptable beautiful screen recorded tutorials made solely by AI".

**Operator:** Grok Bot only. No human ever drives the recorder, opens the editor, or drags a timeline. Every design choice below assumes the user of the tool is an agent that writes files, runs one command, and reads JSON and still images.

**Written:** Friday, Oct 16, 2026 (HST), by Grok Bot. Sources: the screenreel skill and Linux recorder, take 3-1, the Mac render of take 3-1, pilot v5, the earlier context doc, frame analysis of the outputs, and web research on Cursorful, Screen Studio, Tella, Arcade, Supademo and Remotion.

---

## 1. Executive summary

Isaac's verdict on the latest output: "This video is not even a fraction of the smoothness or the synced visually to the audio and clear animations are just not there." The frame analysis agrees. In the screenreel render of lesson 3-1, 74% of the video (68 of 92 seconds) is a frozen picture, motion happens in sudden jumps rather than glides, the cursor is usually not where the click happens, zooms snap in and out within one or two frames, and nothing on screen is tied to the words being spoken.

These are not tuning problems. They come from one root cause: **screenreel tries to beautify a live performance, and the performer (a computer-use subagent that clicks in slow, discrete tool calls and teleports the pointer) is the worst possible performer for this job.** No amount of post-processing can recover a cursor path that was never recorded, element bounds that were never known, or a step mark that was never flushed.

**Recommendation:** turn screenreel into a scripted director. The agent writes a declarative `lesson.yaml` (narration lines plus steps like goto, click, type, select, wait-for). screenreel generates the voice first, gets word timings, plans every action to land on its spoken keyword, drives a dedicated Chrome profile through Playwright on its own Linux display, captures at 60 fps with the system cursor hidden, and renders a synthetic cursor, camera, ripples, captions and an audio mix from exact data. One command, `screenreel make lesson.yaml`, returns the MP4, a timeline and an automatic QA report. Live capture stays as a fallback mode. Effort for the P0 core is estimated at 9 to 13 developer days.

---

## 2. What the take 3-1 evidence shows

### 2.1 Inputs reviewed

| Artifact | Facts |
|---|---|
| `takes/3-1/raw.mov` | 1920x1200, 30 fps, 251.8 s, 7,554 frames |
| `takes/3-1/events.jsonl` | 1 geometry, 9 markers, 9 `move`, 30 `down`/`up` pairs, 60 `key` events in 252 s |
| `takes/3-1/inbox.jsonl` | All 9 marks written with `"t": 0`; timestamps are assigned later, when the recorder drains the inbox |
| `takes/3-1/NOTES.md` | Step 9 mark lost and appended by hand at 236.0 s; Cursorful tab in front at about 189 to 203 s; steps 8 and 9 clicked via DOM tools so no pointer events after 165.7 s; typing not at 70 ms/key |
| `takes/3-1/edit.json` | Default style, background `#14141C` (not TestFiesta), `segments` and `cuts` empty, `zooms: "auto"` |
| `takes/3-1/recorder.log` | `Unknown command. On Linux this tool records. Beautify the take on the Mac.` |
| `screenreel-3-1/out.mp4`, `timeline.json` | 1920x1080, 30 fps, 92.5 s; 7 "No actions" warnings covering most of the source (0 to 40 s, 42 to 63 s, 67 to 92 s, 92 to 112 s, 118 to 145 s, 145 to 166 s, 166 to 252 s) |
| `pilot-3-1-screenreel.mp4` | out.mp4 plus the voice mix done outside the binary; -14.0 LUFS, true peak -0.6 dBTP |
| `pilot-3-1-v5.mp4` | Custom Python pipeline (`build_v5.py`); 108 s, 30 fps; -14.0 LUFS, **true peak +1.9 dBTP** |

### 2.2 Measured smoothness (frame difference analysis)

Each output was decoded at 320x180 grayscale and the mean absolute difference between consecutive frames was computed. A frame counts as "static" below 0.05 and as a "jump" when its difference is above 2 and more than four times both neighbours.

| Metric | screenreel render | v5 custom pipeline | Target |
|---|---|---|---|
| Frames with any motion | 229 of 2,773 (8%) | 2,146 of 3,240 (66%) | Above 85% outside title and end cards |
| Static runs of 1.5 s or more | 13 runs, 68.4 s, 74% of runtime | 6 runs, 24.3 s, 22.5% (10 s of it is the title card) | 0 runs above 2.0 s outside cards |
| Longest static run | 10.0 s (6.3 to 16.3 s) | 9.7 s (title card) | 2.0 s |
| Median difference on moving frames | 2.31 (motion arrives in lumps) | 0.55 (continuous glide) | Below 1.0 |
| Isolated whole-frame jumps | 11 (6.2, 17.3, 42.3, 45.0, 51.6, 63.2, 63.5, 68.6, 69.1, 82.0 s) | 4 | 0 outside deliberate cuts |

### 2.3 What the frames show

- **Cursor teleports and lags its own clicks.** The computer-use subagent warps the pointer, so `events.jsonl` has no path between clicks. Each `move` is logged at the old position right before the next click, for example `62.902 move 954,368` immediately followed by `62.902 down 1202,845`. In the render the cursor therefore sits at the previous spot while the next control changes. At about 45 s it floats in the middle of the form while Next is clicked at the bottom right.
- **Cursor frozen in steps 8 and 9.** At 79 s the cursor sits in the left sidebar at the logged point (80, 548) while the status dropdown is open on the right side of the screen. The DOM-tool clicks produced no events.
- **Cursor sprite is weak.** A thin white arrow with a black outline and a malformed notch, no drop shadow, low contrast on a white UI.
- **Zooms snap.** In the 26 to 30 s window, sampled at 6 fps, the camera goes from full view to zoomed and back between two samples 0.167 s apart, and pans left then right within 0.4 s. The zoom curve (`zoomIn` 0.35 s, `zoomOut` 0.4 s) is built in source time and then squeezed by speed segments, so a 0.35 s ease at 6x lasts about 2 frames.
- **Zooms aim at the wrong place.** At 17 s the typing zoom frames the top-left of the form while the cursor sits far to the right. At 42 s the zoom centres on empty space between Test plans and Milestones.
- **Ripples are not visible** in any of about 40 frames sampled around clicks. Synthetic clicks have down and up in the same millisecond, and the ripple, if drawn, is also squeezed by speed ramps.
- **Loading skeletons are on screen** for about half a second after Next (44.4 to 45.0 s).
- **Privacy leak.** At about 17 s the workspace pill ("Shopfront Team") at the top right is readable while zoomed. It is blurred in other frames.
- **Off-brand look.** Background `#14141C`, not `#1E1B4B` or light indigo; radius 20, not 16.
- **Data hygiene.** The Test runs list at 6 s shows three older "Shopfront smoke" runs from previous takes.
- **v5 is smoother but not good enough.** Its spring cursor and eased camera produce continuous motion, but step changes use a 0.2 s crossfade between two different zoom states, which shows a ghosted double image (visible at 42.0 s). It opens with a 10 s static title card, renders at 30 fps, and its true peak of +1.9 dBTP will clip on some players.

---

## 3. Gap analysis

Severity: **Critical** (blocks the goal), **High** (clearly visible to a viewer), **Medium** (polish), **Low** (configuration).

| # | Capability | Cursorful / state of the art | screenreel today | Gap | Evidence from this take |
|---|---|---|---|---|---|
| 1 | Cursor path | Cursorful replaces jittery input with a smoothed path, adds sway and tilt into turns; Screen Studio turns shaky moves into a steady glide and can loop the cursor | Draws a sprite at logged `move` points; the input has no path because the pointer is warped | Critical | 9 `move` events in 252 s, each at the old position just before a click |
| 2 | Cursor exactly on the click point | Cursor is captured from real input, so it is always where the click lands | Off target whenever the driver warped or used DOM clicks | Critical | 79 s: cursor in sidebar, dropdown open at right; 45 s: cursor mid-form while Next is clicked |
| 3 | Cursor look | Size control, drop shadow so it reads on same-coloured UI, HiDPI system cursors | Thin white arrow, malformed notch, no shadow | High | Cropped still at 79 s |
| 4 | Auto zoom triggering | Cursorful zooms when 2 or more clicks happen within 3 s and keeps following while clicks continue, to avoid motion sickness; Screen Studio and Supademo auto zoom on clicks | Zooms on every click event, including clicks that the renderer sees out of place | High | Zoom on empty area at 42 s |
| 5 | Zoom easing | "Silky, cinematic" zooms and pans; Screen Studio "Focused" vs "Smooth" camera springs | Easing computed in source time, then compressed by speed segments into 1 to 3 frames | Critical | 26 to 30 s: full zoom in and out between samples 0.167 s apart; 11 isolated frame jumps |
| 6 | Zoom framing | Screen Studio re-aims zooms to keep screen and cursor in focus | Centres on a point; no knowledge of the element or its result | High | 17 s: zoom on top-left while cursor is right; v3 cropped out a ticked row (context doc) |
| 7 | Click effect | Orb, pressure ring or ripple per click | Ripple not visible | High | No ripple in about 40 frames sampled around clicks |
| 8 | Typing | Real typing; Screen Studio can show keystrokes on screen | Uncontrolled speed: 60 keys in 1.8 s (30 ms per key); no caret follow | Medium | `events.jsonl` 40.42 to 42.19 s |
| 9 | Dead time | Cursorful: trim and speed up to 10x on a timeline (manual); Tella: trims silences (not re-verified in this pass) | `plan` speeds up waits, but 74% of the output is still static | Critical | 13 static runs, longest 10 s |
| 10 | Speed ramps | Eased speed changes on a timeline | Hard boundaries; zoom and ripple squeezed with the footage | High | Snap zooms at segment edges |
| 11 | Motion blur | Cursorful blurs fast cursor moves; Screen Studio has optics-based blur for cursor, zoom and pan, each adjustable | None | Medium | n/a |
| 12 | Frame rate | Screen Studio exports up to 4K 60 fps; Cursorful up to 4K | 30 fps capture and render | High | ffprobe: 30/1 on all outputs |
| 13 | Text sharpness when zoomed | Remotion Canvas Capture records above display resolution for UI close-ups | Captures at device scale 1, zoom 1.45 upsamples | Medium | Softer text in zoomed frames |
| 14 | Background, frame, shadow | Backgrounds, padding, shadow, inset, saved presets; 10 aspect presets | Present but the default (`#14141C`) shipped | Low | All frames of out.mp4 |
| 15 | Hide browser chrome | One click hides tab strip, address bar, bookmarks | Manual `--crop` flag | Medium | Not used in this take |
| 16 | Off-topic window protection | Extension crops to the browser; follow-zoom stops outside it | Records the whole display | High | Cursorful "Creating shareable link" tab in front for about 28 s, cut by hand |
| 17 | Step marks | Supademo makes a step per click automatically | Marks queued in `inbox.jsonl`, timestamped late, and the last one was lost on stop | High | `inbox.jsonl` all `t: 0`; step 9 added by hand at 236.0 s |
| 18 | Privacy | Cursorful blur segments; Supademo and Arcade replace text in captured HTML | Static rects; y origin bottom-left (bug); no tracking | High | Workspace pill readable at about 17 s |
| 19 | Toasts and widgets | Edit captured HTML (Supademo, Arcade) | None; v3 needed a hand-painted cover | Medium | Context doc, v3 Fiestanaut toast |
| 20 | Edit determinism | Segments on one timeline, never lost | `plan` with step targets overwrites hand cuts; a `zooms` list disables all auto zooms | High | Speeds patched by hand in this run |
| 21 | Warnings | n/a | 7 "No actions" warnings spanning most of the take, none actionable | Medium | `timeline.json` warnings |
| 22 | Voice sync | Supademo and Arcade add AI voiceover; none of the tools reviewed anchors clicks to spoken words | Footage stretched to fit each voice clip; no action is tied to a word | Critical | Clicks land at random points in each line |
| 23 | Audio mix | Cursorful: mic denoising; Screen Studio: volume normalisation | Not in the binary; mixed separately; music not ducked dynamically | High | v5 true peak +1.9 dBTP |
| 24 | Captions | One-click captions from audio, brand styled | None in the binary | Medium | n/a |
| 25 | Platform | Cursorful records Chrome on Linux; Screen Studio is Mac only | Records on Linux, renders only on the Mac, Mac commands need Isaac's approval | Critical | `recorder.log` error |
| 26 | Agent ergonomics | Designed for humans with a GUI editor | 4 to 6 commands across two machines, hand edits to JSON, manual frame review | High | Hours of hand fixes per take |

**The pattern:** every Critical gap traces back to the live performance. Commercial tools are built for a human who moves the mouse smoothly and clicks at a natural pace; they then polish that performance. screenreel inherited the "polish a performance" design but has a performer that cannot perform. For an AI operator the advantage flips: it can describe the performance exactly, so the tool can construct it.

---

## 4. Core insight: produce the recording from a script, not a live performance

### 4.1 Why

An AI agent does not need to perform a demo. It knows ahead of time every page, element, value and line of narration. If screenreel drives the browser itself from a declarative script, it knows:

- the exact bounding box of every element before and after each action, so zoom targets and result regions are exact;
- the exact start and end time of every action, so marks, cuts and voice anchors are automatic;
- when the page is busy and when it has settled, so dead time is removed by construction instead of detected afterwards;
- which elements are private, so masks track them through scroll and zoom;
- the cursor path, because screenreel plans it, so the cursor is rendered from a smooth curve and is always exactly on the click point.

### 4.2 Options

| | (a) Live capture plus a better event log | (b) Scripted driver (recommended) | (c) DOM recording and replay (rrweb style) |
|---|---|---|---|
| How | Keep computer-use clicking; add a CDP listener for element bounds, synchronous marks, window focus checks | screenreel runs Playwright against a dedicated Chrome profile on its own X display, executes `lesson.yaml`, captures with x11grab | Record DOM snapshots and mutations, replay in a headless browser, render frames at any resolution and time step |
| Cursor | Still synthetic between warped points; DOM clicks still invisible unless instrumented | Planned eased Bezier path; real mouse follows the same path so hover states match | Fully synthetic |
| Timing and dead time | Still seconds of tool-call gaps; must be detected and cut | Driver waits for settle, then acts; waits are elastic by construction | Perfect (virtual time) |
| Voice sync | Fit after the fact | Actions scheduled on word timestamps before capture | Perfect |
| Fidelity | Real pixels | Real pixels | Breaks on canvas, cross-origin iframes (Intercom), shadow DOM, auth-gated fonts and CSS, date pickers, animations |
| Effort | Low (3 to 5 days, estimate) | Medium (9 to 13 days for P0, estimate) | High (15+ days, research risk) |
| Agent workflow | Unchanged, still slow and error prone | Faster: write YAML, run one command, read JSON | Same as (b) once it works |

**Recommendation: (b), the scripted driver**, because it fixes all five Critical gaps at the source with real pixels from the real product, and it runs on the box today (Chrome at `/usr/bin/google-chrome`, Node, ffmpeg with `sidechaincompress`, `loudnorm` and `ebur128`, and a 60 fps x11grab test on `:38` returned 360 frames in 6 s). Keep (a) as `screenreel record` for apps that cannot be scripted, with the fixes in section 6.10. Treat (c) as a P2 experiment for re-rendering at 4K or vertical formats.

### 4.3 Architecture

```
lesson.yaml
   |
   v
[1 validate]  schema check, resolve env vars, lint narration (no em dashes, anchors exist)
   |
   v
[2 voice]     ElevenLabs per step (Jack John, eleven_v3) with character timestamps
              -> words.json per step, cached by hash(text, voice, model, settings)
   |
   v
[3 plan]      schedule: each action gets t_travel_start, t_press, t_release, typing timeline,
              anchored to its keyword; camera shots planned from element boxes
   |
   v
[4 rehearse]  optional dry run without recording: selectors resolve, boxes measured,
              settle times learned, screenshots per step (fast, catches errors early)
   |
   v
[5 capture]   dedicated Xvfb display, Chrome kiosk window, Playwright drives the plan,
              x11grab 60 fps with draw_mouse 0, driver writes actions.jsonl on the same clock
   |
   v
[6 retime]    build output timeline: action windows at 1x, waits elastic (cut, hold or
              ease-ramped) so every press lands on its word
   |
   v
[7 render]    camera track, synthetic cursor, ripples, masks, frame, background, captions,
              all computed in OUTPUT time at 60 fps
   |
   v
[8 mix]       voice track placed on the timeline, music bed with sidechain ducking,
              click and key SFX, two-pass loudnorm to -14 LUFS, true peak -1 dBTP
   |
   v
[9 qa]        automatic checks, stills and contact sheet, JSON report
```

Key design points:

- **Dedicated browser.** Playwright `launchPersistentContext` with `channel: "chrome"` and a profile directory owned by screenreel (for example `/workspace/.screenreel/profiles/testfiesta`). Log in once in that profile with the agent's normal browser tools; the session persists. No other tabs or extensions exist in that profile, so a Cursorful tab can never appear.
- **Own display.** screenreel starts `Xvfb :90 -screen 0 1920x1080x24` (exact 16:9, no crop), so the computer-use subagent on `:38` (1920x1200) and the recorder never fight over focus. `--display :38` remains an option.
- **Kiosk window.** Chrome with `--kiosk` or `--app` and a fixed window size so there is no tab strip or address bar to crop or blur. CSS viewport 1440x810 at device scale 1.333 gives 1920x1080 capture pixels with text about 133% of normal size, which reads well on phones.
- **Real mouse mirrors the rendered cursor.** The driver moves the real pointer along the same planned path, with the same timing, in many small `mouse.move` steps. Hover states, tooltips and button highlights then appear in the captured pixels at the moment the rendered cursor passes over them.
- **Settle detection.** After each action the driver waits for: network idle for 300 ms, no DOM mutations in the target region for 250 ms, no running CSS animations, and an optional `wait_for` condition. It records `settled_at`. Everything between the action's reaction window and `settled_at` is elastic in the edit.
- **CSS injection.** On every navigation the driver adds a style tag that hides known noise (Intercom launcher, chat bubbles, cookie banners, optional trial banner) and sets `* { caret-color: auto; scroll-behavior: auto }`. Toasts that matter to the lesson can be kept per step.
- **One clock.** Driver events, marks and the capture share `CLOCK_MONOTONIC`, with the capture's first frame time written to `session.json`.
- **Renderer on Linux.** Evolve the proven `build_v5.py` approach into a module: OpenCV `warpAffine` with float transforms (sub-pixel, no rounding jitter; avoid ffmpeg `zoompan`, which rounds to whole pixels and shimmers), cursor and ripple drawn from vector shapes at 4x and downsampled, frames rendered in parallel chunks across the 8 cores, piped to libx264. Remotion is an alternative compositor with springs and React components, but its Free License only covers organisations of up to 3 people, so check TestFiesta's headcount before adopting it. The Mac Swift renderer becomes optional.

---

## 5. Voice-first timing

The voice is the clock. Footage is fitted to speech, never the other way round.

### 5.1 Flow

1. **Generate narration first.** One request per step. Use `POST /v1/text-to-speech/{voice_id}/with-timestamps`, which returns audio plus character start and end times. If `eleven_v3` is not accepted on that endpoint, use `POST /v1/text-to-dialogue/with-timestamps` (defaults to `eleven_v3`, one input) or align the finished clip with `POST /v1/forced-alignment`, which returns word timings directly. Word timings are derived from character timings by splitting on spaces.
2. **Anchor actions to words.** Each action names a keyword from its step's line (`at: "Save"`). The planner sets the press time to the keyword's start plus `lead` (default -0.08 s, so the click lands just as the word is heard). Cursor travel starts `travel` seconds earlier. Typing starts on its keyword and its per-key rate is chosen to finish before the next anchor.
3. **Pre-roll and tail.** Each step gets 0.25 s of picture before the first word and 0.4 s after the last action's result has settled.
4. **Capture follows the plan.** The driver executes the schedule in real time. If the app is slow, the press slips; the retimer then removes the slip from the following wait so the press still lands on its word in the output.
5. **If an action cannot reach its anchor** (for example, a page load longer than the gap before the keyword, and nothing safe to cut), the planner holds the settled frame with a slow camera drift and reports a `sync_slip` warning with the size of the slip.

### 5.2 Audio mix (built in)

- **Voice track:** clips placed at their planned output times, 20 ms fades, no overlap between steps.
- **Music bed:** optional file, looped with crossfades. Base level about -24 LUFS short term, ducked a further 10 to 14 dB under the voice with `sidechaincompress` keyed by the voice track (attack 40 ms, release 450 ms), or an equivalent gain envelope computed from the voice timeline. 1 s fade in, 2 s fade out.
- **SFX:** a soft click at each press (-24 dB relative to voice), very quiet key ticks per keystroke (-32 dB, randomised among 4 samples), optional whoosh on step transitions (-28 dB).
- **Loudness:** two-pass `loudnorm` to -14 LUFS integrated, true peak at most -1.0 dBTP, LRA at most 11. Measured values go into the JSON result.
- **Captions:** built from the same word timings. Chunks of up to 42 characters, at most 2 lines, shown from the first word's start to 0.2 s after the last word's end. Burned in with the brand style when `captions: burn`, and always written as `captions.srt` and `captions.vtt`.

---

## 6. Detailed requirements and acceptance criteria

All pixel values refer to the 1920x1080 output unless stated. All times are output time unless stated. "Fail" means `make` returns `"ok": false` unless the check is listed as a warning.

### 6.1 Output

| ID | Requirement | Acceptance |
|---|---|---|
| OUT-1 | 1920x1080, 60 fps constant, H.264 High, yuv420p, CRF 16 to 18, `+faststart`, AAC 48 kHz 192 kbps | ffprobe confirms every field; `avg_frame_rate` = 60/1 |
| OUT-2 | Capture at 60 fps | Dropped or duplicated capture frames below 0.5% (from capture timestamps) |
| OUT-3 | Preview mode | `--preview` renders 960x540 at 30 fps in under 25% of full render time |

### 6.2 Cursor

| ID | Requirement | Acceptance |
|---|---|---|
| CUR-1 | Exactly one cursor; the system cursor is never captured | x11grab `-draw_mouse 0` plus Xvfb without a cursor; QA template match finds exactly 1 cursor in every sampled frame where the cursor is visible |
| CUR-2 | Cursor position is a continuous function sampled per output frame | No frame-to-frame cursor jump above 60 px except across a step cut, where the cursor fades out and in over at least 6 frames |
| CUR-3 | Travel follows a curved, eased path | Cubic Bezier with control points offset 6 to 14% of the distance perpendicular to the line, alternating side; minimum-jerk speed profile (ease in and out); peak speed below 2,400 px/s |
| CUR-4 | Travel duration scales with distance | `duration = clamp(0.30 + 0.00045 * distance_px, 0.35, 0.95)` seconds; no travel shorter than 0.35 s |
| CUR-5 | Lands before it clicks | Cursor arrives at least 0.12 s before the press and stays still until release |
| CUR-6 | Exactly on the click point | At the press frame, the hotspot is within 2 px of the action point (element centre, or explicit offset) after all camera transforms |
| CUR-7 | Readable sprite | Vector arrow rendered at 4x and downsampled, dark fill (#0F172A), 2 px white outline, soft drop shadow (blur 6, offset 2,3, 35% black), height 40 to 48 px at zoom 1; hotspot at the tip |
| CUR-8 | Press feedback | Scale to 0.86 over 80 ms on press, back to 1.0 over 140 ms on release |
| CUR-9 | Idle hide (P1) | After 2.5 s still with no upcoming action within 1 s, fade out over 0.3 s; fade in 0.25 s before the next travel |
| CUR-10 | Sway (P2) | Tilt up to 8 degrees into direction changes, settling with a damped spring |

### 6.3 Camera and zoom

| ID | Requirement | Acceptance |
|---|---|---|
| CAM-1 | The camera is one continuous track of (centre x, centre y, scale), computed in output time after retiming | Camera never changes instantly except at a declared cut; QA finds 0 isolated frame jumps outside cuts |
| CAM-2 | Eased motion | Critically damped spring (default stiffness tuned so a 1.0 to 1.6 zoom settles within 0.7 s) or quintic ease in and out with duration 0.6 to 1.0 s |
| CAM-3 | Rate limits | Zoom rate `abs(d ln(scale)/dt)` at most 1.2 per second; pan speed at most 0.6 viewport widths per second; no reversal of pan direction within 0.5 s |
| CAM-4 | Frame the action and its result | Shot rectangle = union of the target element box and the result region (from `result:` selector, or the box of DOM mutations within 800 ms after the action), padded 10%, fitted at scale at most 1.6 (1.8 when capture is at device scale 2). The result region stays 100% inside the frame for at least 0.8 s after the action settles |
| CAM-5 | Group nearby actions | If the next action starts within 2.5 s and its shot fits at a scale within 15% of the current one, pan instead of zooming out and in (matches Cursorful's anti-motion-sickness rule) |
| CAM-6 | Follow typing | During typing the shot contains the whole field box; if text grows past the right 20% of the shot, pan with the caret |
| CAM-7 | Breathing room | Between steps return to a wide shot (scale 1.0 to 1.1) unless the next step's first target is in the current shot |
| CAM-8 | Never crop the click result | QA check: at release + 0.5 s, the result region box (logged by the driver) is fully inside the camera rectangle |
| CAM-9 | Overrides | `camera: {auto: true, add: [...], remove: [ids], tweak: {id: {...}}}`. Adding manual shots never disables auto shots |

### 6.4 Click effect

| ID | Requirement | Acceptance |
|---|---|---|
| CLK-1 | Ripple at the exact press point | Starts on the press frame, centred within 2 px of the cursor hotspot |
| CLK-2 | Visible | Ring 4 px, brand indigo #4F46E5, radius 10 to 44 px, ease-out over 450 ms, opacity 0.9 to 0, inner soft fill #6366F1 at 35%; visible in at least 24 consecutive frames at 60 fps |
| CLK-3 | Never compressed | Effects are drawn in output time, after retiming, so speed ramps cannot shorten them |

### 6.5 Typing

| ID | Requirement | Acceptance |
|---|---|---|
| TYP-1 | Real per-key typing | `keyboard.type` with a per-key delay of 45 to 80 ms, jitter plus or minus 20%, plus 60 to 120 ms at word boundaries; rate chosen by the planner to finish before the next anchor |
| TYP-2 | Verify | After typing, the field value equals the expected text; otherwise retry once (clear and retype), then fail with `type_mismatch` |
| TYP-3 | Long strings | Strings above 40 characters may be typed at a faster rate (down to 30 ms/key); never pasted invisibly unless `paste: true` |
| TYP-4 | Keystroke overlay (P2) | Shortcut keys (for example Ctrl+K) shown as brand-styled keycaps for 0.8 s |

### 6.6 Dead time, transitions and pacing

| ID | Requirement | Acceptance |
|---|---|---|
| IDL-1 | No dead picture | Outside title and end cards, no run of static frames longer than 2.0 s. During long narration over a settled screen, the camera performs a slow drift (scale change at most 3% over the hold) so the picture is alive |
| IDL-2 | Loading states | Spinners and skeletons visible for at most 0.4 s; longer waits are cut with a 6 to 10 frame crossfade between the pre-wait and settled frames at the same camera position, or ramped with an eased speed curve |
| IDL-3 | Eased speed ramps | Speed changes ease over at least 0.3 s; max speed 8x; cursor, camera and effects are not affected because they live in output time |
| TRN-1 | Step transitions | Continuous camera glide when the page is the same; for navigations, a crossfade of 8 to 12 frames with the camera at the same rectangle on both sides (no ghosted double image of two different zoom states for more than 4 frames) |
| TRN-2 | Step chip | Optional step label chip animates in over 250 ms and out over 200 ms, brand styled, never overlapping the shot rectangle |
| TRN-3 | Title and end cards | Animated (logo and title ease in), at most 3.5 s, voice may start over the title |

### 6.7 Look

| ID | Requirement | Acceptance |
|---|---|---|
| LOOK-1 | Brand from the lesson, never from code | `style.background` (TestFiesta: `#1E1B4B` or a light indigo gradient around `#EEF2FF`), `radius: 16`, accent `#4F46E5` |
| LOOK-2 | Frame and shadow | Window width at least 82% of canvas width; shadow blur 40 px, y offset 16 px, 35% opacity |
| LOOK-3 | Motion blur (P1) | Temporal supersampling: 4 to 8 subframes per output frame, 180 degree shutter, applied to cursor and camera motion only when speed exceeds a threshold. Toggle `motion_blur: off | cursor | all` |
| LOOK-4 | Sharp text when zoomed (P1) | Capture at device scale 2 (2880x1620) when the 60 fps drop rate stays below 0.5%; otherwise cap zoom at 1.5 |
| LOOK-5 | No em dashes in any rendered text | Lint fails the lesson if `\u2014` appears in captions, chips or cards |

### 6.8 Privacy and overlays

| ID | Requirement | Acceptance |
|---|---|---|
| PRV-1 | Masks by selector | `mask: [{selector, mode: blur | replace, text}]`. The driver logs the element box on every layout change and scroll; the renderer interpolates the box per frame |
| PRV-2 | Masks survive zoom | Masks are applied in source space before the camera transform, so zoom and pan cannot reveal them |
| PRV-3 | Replace instead of blur when possible | `replace` sets the element's text via DOM before capture (for example the workspace name), which looks natural and cannot leak |
| PRV-4 | Verify | QA OCRs every 0.5 s of output inside and around mask regions and fails if any string on the lesson's `never_show` list appears |
| PRV-5 | Coordinates | All rects use a top-left origin in source pixels, y down. Fix the bottom-left blur bug; unit test with a rect at (0, 0, 100, 50) |
| OVL-1 | Noise hidden by CSS | Lesson-level `hide:` selectors injected on every navigation; default list includes the Intercom launcher and messenger frame |
| OVL-2 | Toasts | Hidden by default; `keep_toasts: true` on a step keeps them (for example "Test run created") |

### 6.9 Pipeline and data

| ID | Requirement | Acceptance |
|---|---|---|
| PIP-1 | Deterministic | Same lesson, capture and voice cache produce byte-identical `plan.json`, `timeline.json` and the same frame hashes for a re-render |
| PIP-2 | Plan merges, never overwrites | Three layers: `auto.json` (regenerated), `overrides.json` (agent edits, never touched by the tool), effective plan = merge. Every generated item has a stable id (`step-6.click-1`) so overrides refer to it. Re-running `plan` with step targets keeps every override |
| PIP-3 | Idempotent and cached | Voice cached by content hash; capture reused with `--reuse-capture`; render can redo one step with `--only step-6` |
| PIP-4 | Marks | Automatic in scripted mode. In live mode, `mark` writes the event synchronously with its own monotonic timestamp at call time, and `record stop` drains all pending marks before closing. No mark may be lost |
| PIP-5 | Actionable warnings | Each warning has `code`, `step`, `t`, `message`, `fix`. "No actions" never appears in scripted mode; in live mode it is emitted once per step, not once per gap |
| PIP-6 | Seed and cleanup hooks | `setup:` and `teardown:` steps (or a shell hook) so leftover data from earlier takes (three "Shopfront smoke" runs) is removed or hidden before capture |
| PIP-7 | Take format stable | Take folder keeps `raw` capture, `actions.jsonl`, `plan.json`, `timeline.json`, `qa.json`, so the Mac renderer can still read it |

### 6.10 Live fallback mode fixes (keep `record`, make it honest)

- Fix the inbox race: the recorder reads the inbox size after draining, so a mark written between the two calls is skipped. Use a line-offset cursor or a FIFO.
- `record stop` must drain the inbox before killing the recorder.
- Log real pointer events and also a CDP `Input` listener in the target tab, so DOM-tool clicks are visible.
- Log window focus and the active URL; auto-cut any span where the focused window is not the target app.
- Mark synthetic warps (`move` with zero travel) as `"synthetic": true` so the renderer plans a path.

---

## 7. Agent-facing interface

### 7.1 Commands

```
screenreel doctor                         # display, Chrome, ffmpeg, profile login state, ElevenLabs key present
screenreel login --profile testfiesta     # opens the profile headed on :38 so the agent can log in once
screenreel inspect lesson.yaml [--step 6] # dry run: resolves selectors, prints boxes and accessible names, writes screenshots
screenreel make lesson.yaml [--preview] [--only step-6] [--reuse-capture] [--reuse-voice]
screenreel qa <take>                      # re-run checks on an existing render
screenreel frames <take> --markers | --every 5 | --at 12.5,30
screenreel record start|mark|stop         # live fallback mode
```

`make` is the only command needed in the normal loop. `inspect` replaces most computer-use exploration: it lists clickable elements by role and accessible name on each page, so the agent can write selectors without driving the browser by hand.

### 7.2 `make` result (stdout, one JSON object)

```json
{
  "ok": true,
  "take": "/workspace/screenreel/takes/3-1-20261016-1415",
  "outputs": {
    "video": ".../out.mp4",
    "preview": null,
    "captions_srt": ".../captions.srt",
    "timeline": ".../timeline.json",
    "contact_sheet": ".../qa/sheet.png",
    "stills": [".../qa/step-1.png", ".../qa/step-2.png"]
  },
  "duration": 96.4,
  "steps": [
    {"id": "step-1", "label": "Create test run", "t0": 3.2, "t1": 13.9,
     "actions": [{"id": "step-1.click-1", "press": 9.84, "anchor": "Create", "anchor_t": 9.92, "slip": -0.08}]}
  ],
  "audio": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.2, "lra": 6.1},
  "qa": {
    "passed": 14, "failed": 0,
    "checks": [
      {"id": "one_cursor", "ok": true, "frames_checked": 193},
      {"id": "cursor_on_target", "ok": true, "max_error_px": 1.4},
      {"id": "result_in_frame", "ok": true},
      {"id": "no_static_over_2s", "ok": true, "longest_s": 1.6},
      {"id": "no_frame_jumps", "ok": true},
      {"id": "url_allowlist", "ok": true},
      {"id": "privacy_ocr", "ok": true},
      {"id": "sync_within_150ms", "ok": true, "max_slip_s": 0.11},
      {"id": "loudness", "ok": true},
      {"id": "fps_60", "ok": true}
    ]
  },
  "warnings": [
    {"code": "slow_page", "step": "step-5", "t": 44.1, "message": "Add test cases took 2.3 s to settle; cut to 0.3 s", "fix": null}
  ],
  "timings_s": {"voice": 0.4, "capture": 71.0, "render": 182.5, "qa": 21.3}
}
```

### 7.3 Automatic QA checks

| Check | Method | Threshold |
|---|---|---|
| `one_cursor` | Template match of the cursor sprite on every 10th frame, plus a check that no system cursor shape exists in the raw capture | Exactly 1 when visible |
| `cursor_on_target` | Compare the rendered hotspot at each press with the driver's action point mapped through the camera | At most 2 px |
| `zoom_on_target` | Target box inside the camera rectangle from press minus 0.2 s to release plus 0.5 s | 100% |
| `result_in_frame` | Result region inside camera rectangle 0.5 s after settle | 100% |
| `no_static_over_2s` | Frame difference analysis (as in section 2.2) | No run above 2.0 s outside cards |
| `no_frame_jumps` | Isolated jump detector | 0 outside declared cuts |
| `url_allowlist` | Driver logs the URL and window title per frame; any frame outside the lesson's allowed origins fails | 0 frames |
| `privacy_ocr` | Tesseract on mask neighbourhoods every 0.5 s | 0 hits from `never_show` |
| `sync` | Press time vs anchor word start | Within 150 ms |
| `loudness` | `ebur128` on the final file | -14.0 plus or minus 0.5 LUFS, true peak at most -1.0 dBTP |
| `fps_60`, `size` | ffprobe | 60/1, 1920x1080 |
| `captions` | Every caption inside the video, no overlap, no em dash | Pass |
| `black_or_blank` | Mean luma and variance per frame | No black or blank frame outside fades |

### 7.4 Sample `lesson.yaml` for TestFiesta lesson 3-1

Selectors use Playwright role and text locators, which survive styling changes. Exact names should be confirmed with `screenreel inspect` before the first full make.

```yaml
lesson: "3-1"
title: "Create a run and mark a result"
output: /workspace/academy-video/lesson-3-1.mp4

app:
  base_url: ${TESTFIESTA_URL}            # practice org, set in the environment
  profile: testfiesta                    # dedicated Chrome profile, already logged in
  viewport: {width: 1440, height: 810, scale: 1.333}
  allowed_origins: ["${TESTFIESTA_URL}"]
  hide:                                  # injected as CSS on every navigation
    - "#intercom-container"
    - ".intercom-lightweight-app"
  mask:
    - {selector: "[data-testid=workspace-switcher]", mode: replace, text: "Shopfront Team"}
  never_show: ["@gmail.com", "isaac"]

style:
  background: "#1E1B4B"                  # or {gradient: ["#EEF2FF", "#E0E7FF"]}
  radius: 16
  accent: "#4F46E5"
  cursor_scale: 1.0
  max_zoom: 1.6
  motion_blur: cursor
  captions: burn                         # burn | sidecar | off
  step_chip: true
  title_card: {seconds: 3.0}
  end_card: {seconds: 3.0, line: "Next lesson in TestFiesta Academy"}

voice:
  provider: elevenlabs
  voice_id: 7EzWGsX10sAS4c9m9cPf         # Jack John
  model: eleven_v3
  lead: -0.08                            # press lands 80 ms before the keyword starts

audio:
  music: /workspace/academy-video/audio/3-1/music.mp3
  duck_db: -12
  sfx: {click: default, keys: default, whoosh: off}
  lufs: -14
  true_peak: -1.0

setup:
  - goto: "/projects/SHOP/test-runs"
  - script: archive_runs_named          # hook: hide leftovers such as old "Shopfront smoke" runs
    args: {prefix: "Cart checkout smoke"}

intro:
  say: "Let's create a test run and record a result. A run is one pass through the cases you pick, so the team has one place to see what was tested and how it went."

steps:
  - id: create
    label: "Create a test run"
    say: "Open Test runs and click Create test run. Results live inside a run, not in the case itself, so the same case can be run again and again."
    do:
      - goto: "/projects/SHOP/test-runs"
      - click: {role: button, name: "Create test run"}
        at: "Create"
        result: {text: "Create test run", role: heading}

  - id: name
    label: "Name the run"
    say: "Name it Cart checkout smoke, October 16. A clear name tells everyone exactly which pass they're reading later."
    do:
      - click: {role: textbox, name: "Name"}
        at: "Name"
      - type: "Cart checkout smoke - Oct 16"
        at: "Cart"
        expect_value: "Cart checkout smoke - Oct 16"

  - id: start-date
    label: "Start date"
    say: "Pick a Start Date. The name and both dates are required, which ties every run to a real window of time."
    do:
      - click: {label: "Start Date"}
        at: "Start"
        result: {role: dialog}
      - click: {role: gridcell, name: "16", within: {role: dialog}}
        at: "required"

  - id: due-date
    label: "Due date"
    say: "Then pick a Due Date. A run with a deadline lets the team see whether testing is on track for the release."
    do:
      - click: {label: "Due Date"}
        at: "Due"
        result: {role: dialog}
      - click: {role: gridcell, name: "23", within: {role: dialog}}
        at: "deadline"
    note: "Known display bug: the field may show 10/22/26 after picking the 23rd. Narration avoids reading the date aloud."

  - id: next
    label: "Next: choose cases"
    say: "Click Next. This form only sets up the run. What you actually test comes on the next screen."
    do:
      - scroll_into_view: {role: button, name: "Next"}
      - click: {role: button, name: "Next"}
        at: "Next"
        wait_for: {text: "Add test cases"}

  - id: tick
    label: "Tick the case"
    say: "Tick only the case you need, here SHOP 1520, guest can add an item to the cart. A focused run shows clearly whether this change works."
    do:
      - click: {role: checkbox, within: {role: row, has_text: "SHOP-1520"}}
        at: "Tick"
        result: {role: row, has_text: "SHOP-1520"}

  - id: save
    label: "Save the run"
    say: "Click Save, and the run shows up in the list. Each case you add gets its own id inside the run, so its result is tracked here without changing the original case."
    keep_toasts: true
    do:
      - click: {role: button, name: "Save"}
        at: "Save"
        wait_for: {text: "Cart checkout smoke - Oct 16"}
        result: {role: row, has_text: "Cart checkout smoke - Oct 16"}

  - id: open
    label: "Open the run and the case"
    say: "Open the run, then click the case. This is where you record what the screen actually did."
    do:
      - click: {role: link, name: "Cart checkout smoke - Oct 16"}
        at: "run"
        wait_for: {text: "Guest can add an item to the cart"}
      - click: {text: "Guest can add an item to the cart"}
        at: "case"
        result: {role: complementary}

  - id: passed
    label: "Mark Passed"
    say: "Set the status to Passed only when you ran the steps and the screen matched. A fake pass hides a bug that customers then find, and the run's progress updates right away."
    do:
      - click: {role: button, name: "Active"}
        at: "status"
        result: {role: listbox}
      - click: {role: option, name: "Passed"}
        at: "Passed"
        result: {text: "100%"}
      - hold: 1.0
```

Fields: `at` must be a word that appears in that step's `say`; the linter rejects missing anchors. `result` tells the camera what must stay in frame after the action. `wait_for` and `expect_value` are hard assertions; a failure stops the capture with the step id and a screenshot.

---

## 8. Self-verification loop and failure modes

### 8.1 Loop the agent runs

1. Write or update `lesson.yaml` from the lesson copy (narration explains why each step matters; only facts from the lesson).
2. `screenreel inspect lesson.yaml`. Fix any selector that did not resolve. This takes seconds and catches most errors before any voice credits or capture time are spent.
3. `screenreel make lesson.yaml --preview`. Read the JSON. If `ok` is false, read the failing check and its `fix`.
4. Open `qa/sheet.png` and the per-step stills with Read. Look for: one cursor on the target, the result visible, readable text, brand frame, no private data.
5. Fix by editing `lesson.yaml` or `overrides.json` only, never generated files. Re-run with `--only <step>` and `--reuse-voice` when the narration did not change.
6. When the preview passes, `screenreel make lesson.yaml` (full 60 fps). Confirm QA passes again.
7. Stop after 3 failed iterations on the same check and report the blocker with the stills, instead of shipping.
8. Deliver the MP4 path, timeline and QA summary for Isaac's review. Nothing is published.

### 8.2 Failure modes

| Failure | Detection | Handling |
|---|---|---|
| Selector not found or ambiguous | `inspect` and driver locator timeout (default 8 s) | Fail with step id, screenshot and the 10 nearest accessible names |
| Session expired | Redirect to a login URL | Fail `login_required`; agent runs `screenreel login` and logs in once |
| App slow or spinner stuck | Settle timeout (default 15 s) | Retry the action once if it is idempotent, else fail; long settles are cut, not shown |
| Data drift (leftover runs, renamed case) | `setup` hooks; `expect` assertions | Run cleanup hook; fail early if seed data is missing |
| Date picker or timezone quirk | `expect_value` mismatch | Warn; narration should not read values the app displays unreliably (Due Date shows 10/22/26 when 23 is picked) |
| Toast or widget covering the target | Element not visible or covered (Playwright actionability check) | Hide via CSS list; wait for toast to leave; fail with screenshot if still covered |
| Wrong window or tab in front | URL allowlist per frame; dedicated profile | Cannot occur in scripted mode; live mode auto-cuts and warns |
| Voice longer than the action span | Planner | Hold settled frame with camera drift; warn if hold exceeds 2.0 s |
| Action cannot reach its anchor | Retimer | Shift press up to 0.4 s late with `sync_slip` warning; above that, fail and suggest moving the anchor word |
| ElevenLabs error or quota | HTTP status | Retry with backoff 3 times; reuse cache; fail `voice_unavailable` |
| Capture drops frames | Capture timestamps | Fail if above 0.5%; suggest device scale 1.333 or a lower CRF preset |
| Privacy leak | OCR check | Fail; add a `mask` or `replace` entry |
| Two cursors | Template match | Fail; usually a missing `-draw_mouse 0` or a cursor inside a captured iframe |
| Non-determinism between runs | Hash compare of plan and timeline | Warn; log which input changed |

---

## 9. Roadmap

Effort figures are **estimates** in developer days for one developer (or one coding agent with review), assuming Linux only.

### P0: scripted, synced, smooth (estimate 9 to 13 days)

| Item | Estimate |
|---|---|
| Lesson schema, linter (anchors, em dashes), env resolution | 1 day |
| Playwright driver on a dedicated profile and Xvfb display, kiosk window, CSS injection, settle detection, `actions.jsonl` with boxes and results, real mouse along planned paths, per-key typing with verify | 3 to 4 days |
| x11grab capture at 60 fps on the same clock; `inspect` dry run | 1 day |
| Voice step with timestamps and cache; word extraction; anchor scheduler and retimer | 1.5 to 2 days |
| Renderer in output time: camera spring with rate limits and result framing, synthetic cursor, ripple, brand frame and background, chips, cards; parallel chunked rendering | 2 to 3 days |
| Built-in mix: voice placement, ducked music, click and key SFX, two-pass loudnorm, captions SRT and burn-in | 1 day |
| `make` JSON result and core QA (one cursor, cursor on target, result in frame, static runs, jumps, LUFS, fps, URL allowlist) | 1 day |

Exit criteria: lesson 3-1 rebuilt from `lesson.yaml` with one command, zero hand edits, all section 2.2 targets met, sync within 150 ms on every anchored action, QA green.

### P1: polish and robustness (estimate 6 to 9 days)

- Motion blur via subframe supersampling (1.5 days).
- Device scale 2 capture for sharp zooms, with automatic fallback (1 day).
- Mask tracking by selector with DOM text replacement, privacy OCR check (1.5 days).
- Overrides layer with stable ids and merge semantics; `--only` step re-render (1 day).
- Cursor idle hide; eased speed ramps for unavoidable waits (1 day).
- Live mode fixes from section 6.10 (inbox race, drain on stop, CDP click log, focus auto-cut, top-left blur origin) (1 to 2 days).
- Lesson library helpers: reusable `setup` hooks per product (1 day).

### P2: reach (estimate 8 to 15 days, optional)

- Vertical 9:16 and square exports re-framed from the same camera plan (2 days).
- Keystroke overlay, cursor sway (1.5 days).
- rrweb style DOM replay or virtual-time frame stepping for 4K renders (research, 5 to 10 days).
- Remotion compositor, if licensing allows and the Python renderer becomes a bottleneck (3 days).

### What to cut

- Any GUI editor or timeline UI. The agent edits YAML.
- Webcam, microphone, denoising and shareable links (no human, no live voice, nothing is published).
- Feature parity work on the Mac Swift renderer. Freeze it; keep the take format readable.
- Auto zoom heuristics on raw pointer data in scripted mode (the driver knows the targets).
- "No actions" style gap warnings in scripted mode.
- Windows support.

---

## 10. Linux-first deployment

- Runs entirely on the agent's box: Linux, 8 cores, Chrome at `/usr/bin/google-chrome`, Node, Python 3, ffmpeg with `x11grab`, `sidechaincompress`, `loudnorm` and `ebur128`, `xvfb-run` present.
- To install: `playwright` (Python or Node package; use `channel: "chrome"` to reuse the installed Chrome), `opencv-python-headless`, `numpy`, `Pillow`, `tesseract-ocr` for the privacy check.
- Default capture display: a private `Xvfb :90` at 1920x1080 started and stopped by screenreel. The agent's shared display `:38` (1920x1200) stays free for the computer-use subagent and for `screenreel login`. `--display :38` with an automatic 1920x1080 crop is supported when a visible run is wanted.
- The Mac is optional: it can render an existing take folder, but nothing in the loop requires it or Isaac's shell approvals.
- Every command prints one JSON object and exits non-zero on failure, as today.

---

## 11. How this changes the agent's workflow

| Today (take 3-1) | With scripted screenreel |
|---|---|
| Computer-use subagent clicks for about 4 minutes, with seconds of dead time per action | Driver runs the lesson in roughly the narration length plus page waits (about 1.5 minutes) |
| Marks sent from a separate shell, one lost | Marks are the steps; nothing to send |
| Leftover Cursorful tab cut by hand | Dedicated profile, nothing else can appear |
| `plan` re-run overwrote cuts; speeds patched by hand | Overrides never touched by the tool |
| Voice mixed outside; music not ducked | Mix, ducking, loudness and captions built in |
| Copy take to Mac, render with Isaac's approval | Everything on the box |
| Eyeball stills to find problems | QA report names the failing step and check |

---

## Appendix A: sources

- Cursorful product page (cursorful.com): auto zooms and pans that follow clicks, manual zooms, segments for zoom, speed, camera and blur, speed up to 10x, auto crop, hide browser frame, smooth cursor path, cursor size, click animations (orb, ring, ripple), motion blur, sway, cursor shadow, captions, 4K local rendering, aspect presets. Auto zoom triggers on 2 or more clicks within 3 s.
- Screen Studio (screen.studio and guides): auto zoom on clicks, smooth cursor, optics-based motion blur for cursor, zoom and pan, "Focused" and "Smooth" camera animation, re-aimed zooms, 4K 60 fps export, keystroke display, loudness normalisation.
- Supademo and Arcade: HTML capture that keeps text editable for redaction, automatic step per click, auto zoom, AI voiceover.
- Tella: scene based recording with automatic trimming of silences (from general knowledge; not re-verified in this research pass).
- Remotion: React based programmatic video, Canvas Capture above display resolution; Free License for organisations of up to 3 people, Company License otherwise.
- ElevenLabs API: `text-to-speech/{voice_id}/with-timestamps` (character timings), `text-to-dialogue/with-timestamps` (eleven_v3 default), `forced-alignment` (word timings).
- Local: `/workspace/screenreel-for-bot/SKILL.md`, `/workspace/screenreel-for-bot/linux/screenreel`, `/workspace/screenreel-for-bot/takes/3-1/`, `/workspace/academy-video/screenreel-3-1/`, `/workspace/academy-video/pilot-3-1-v5.mp4`, `/workspace/academy-video/build_v5.py`, `/workspace/academy-video/docs/screenreel-context-for-cursor-agent.md`. Frame analysis scripts and extracted stills: `/workspace/srplan/`.
