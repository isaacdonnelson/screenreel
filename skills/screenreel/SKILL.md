---
name: screenreel
description: >
  Record a SaaS product and render a polished demo video with eased zoom,
  a smooth enlarged cursor, click ripples, and a rounded window on a brand
  background. Use when asked to screen record, record a demo, beautify a
  screen recording, or make a product walkthrough. Runs the screenreel CLI.
---

# Screenreel

Record the screen, then render a demo from an edit list. The brand color, zooms, blurs, covers, and speed live in `edit.json`. Nothing about a particular product is baked into the tool.

The clicking computer and the rendering computer can be different. The take folder is the same on both.

- Linux, where the bot clicks: `/Users/isaacdonnelson/git/screenreel/linux/screenreel`
- Mac, where the picture is rendered: `screenreel` on `PATH` (`~/.local/bin/screenreel`)

Source: `/Users/isaacdonnelson/git/screenreel`.

If the Mac binary is missing:

```bash
make -C /Users/isaacdonnelson/git/screenreel install
```

Every command prints one JSON object on stdout. A non-zero exit means `"ok": false`. Read `warnings` before you deliver the video.

## Where to record

Record on the machine that can click the product.

On the Linux display, from the repo copy on that machine:

```bash
python3 linux/screenreel doctor
python3 linux/screenreel record start --take takes/<name>
python3 linux/screenreel mark --take takes/<name> --label "step 2: name"
python3 linux/screenreel record stop --take takes/<name>
```

`doctor` must see `DISPLAY`. The capture hides the X cursor. Copy the take folder to the Mac when the raw file is closed.

On the Mac, only when something on the Mac will do the clicking:

```bash
screenreel doctor
screenreel record start --take takes/<name>
screenreel mark --take takes/<name> --label "step 2: name"
screenreel record stop --take takes/<name>
```

`doctor` names the app that needs Screen Recording and Accessibility. That permission sticks to the app that launches the binary. An agent shell is not Terminal. Key characters are never logged.

Prefer a display near 1920x1200, the browser full screen, and a steady pace. Optional flags: `--display <id>`, `--crop x,y,w,h`, `--fps 30`.

## Edit list

`screenreel plan --take takes/<name>` on the Mac writes speed segments and cuts of long waits into `edit.json`. It keeps `"zooms": "auto"`. A `secret` event becomes a timed blur.

Then change the file when the picture needs it:

- `style.background`, `padding`, `radius`, `shadow`, `cursorScale`, `zoomScale`, `zoomIn`, `zoomHold`, `zoomOut`
- For a TestFiesta lesson, background `#1E1B4B` and radius `16` match the product. Other products pass their own hex.
- `blurs` and `covers`: `{ "x", "y", "width", "height", "t0", "t1" }`. A cover also takes `color`. Times are source seconds. Omit them to cover the whole take.
- `segments`: `{ "start", "end", "speed" }`. `cuts`: `{ "start", "end" }`.
- `steps`: `{ "label", "target" }` so `plan` fits that marker's step to the voice length, speeding waits first.
- `zooms`: `"auto"`, or `{ "t", "x", "y", "scale", "hold" }`.
- `audio`, `overlays`, `duck`, and `lufs` are kept for the voice mix. The binary does not mix them.

## Render and check

On the Mac:

```bash
screenreel beautify --take takes/<name> --preview
screenreel beautify --take takes/<name>
screenreel frames --take takes/<name> --markers
screenreel frames --take takes/<name> --every 5
screenreel timeline --take takes/<name>
```

`--preview` writes `preview.mp4` at 960x540. The full render writes `out.mp4` at the canvas size, normally 1920x1080. `timeline.json` has each marker's output `t0` and `t1` so a voice clip can be lined up later.

Open the stills. Check one cursor, readable type, blurred private text, and zooms that still show the result of the click. If a warning says a zoom is outside the frame or a long still has no actions, change `edit.json` and run `beautify` again.

## Scripted lessons

`python3 linux/screenreel make lesson.yaml` is the Linux path for a lesson file. It starts Xvfb `:90` at 1920x1080, opens Chrome with `launchPersistentContext` on `.screenreel/profiles/<profile>`, captures x11grab at 60 fps with `-draw_mouse 0`, and draws the cursor and camera in output time. The sample is `lessons/testfiesta-3-1.yaml`. URL and credentials are `${TESTFIESTA_URL}`, `${TESTFIESTA_USER}`, and `${TESTFIESTA_PASSWORD}`. Never write those values into the lesson.

```bash
python3 -m pip install -r linux/requirements.txt
python3 linux/screenreel doctor
python3 linux/screenreel lint lessons/testfiesta-3-1.yaml
python3 linux/screenreel login --profile testfiesta
python3 linux/screenreel make lessons/testfiesta-3-1.yaml --plan-only --allow-estimated-voice
python3 linux/screenreel make lessons/testfiesta-3-1.yaml --preview
```

`make` prints one JSON object. `ok: false` is a non-zero exit. Audio mix, burned captions, and privacy OCR are still reported as `not_implemented`. A live `record` take still does not mix voice.

Return the `out.mp4` path and the timeline.

Do not launch Cursorful.
