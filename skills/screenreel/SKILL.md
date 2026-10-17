---
name: screenreel
description: >
  Record a SaaS product and render a polished demo video with eased zoom,
  a smooth enlarged cursor, click ripples, and a rounded window on a brand
  background. Use when asked to screen record, record a demo, beautify a
  screen recording, or make a product walkthrough. Runs the screenreel CLI.
---

# Screenreel

Record on the computer that can click the product. Render on the Mac. The take folder is the same in both places. Brand color, zooms, blurs, covers, and speed live in `edit.json`.

Every command prints one JSON object. A non-zero exit means `"ok": false`. Read `warnings` before you deliver the video.

## Install on the Linux computer

If `screenreel doctor` is not found, install it. On a Grok Bot computer the files land in `/workspace/screenreel`, which survives a computer update. Then put `~/.local/bin` on `PATH`.

```bash
curl -fsSL https://raw.githubusercontent.com/isaacdonnelson/screenreel/main/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
screenreel doctor
```

`doctor` must report `"ok": true` and a `DISPLAY`. The capture hides the X cursor. Repo: https://github.com/isaacdonnelson/screenreel

Three ways to drive the same recorder:

- CLI: `screenreel doctor`, `screenreel record start --take takes/<name>`, `screenreel mark --take takes/<name> --label "step 2: name"`, `screenreel record stop --take takes/<name>`
- MCP, already connected: call `screenreel_doctor`, `screenreel_record_start`, `screenreel_mark`, `screenreel_record_stop`. The command on this computer is `python3 /workspace/screenreel/linux/mcp_server.py`. In Grok, add that as a custom MCP command plugin. From a checkout, `grok mcp add screenreel -- python3 linux/mcp_server.py`.
- API, on this computer: `screenreel api` then `curl -s localhost:8765/v1/doctor`. Record with POST `/v1/record/start`, `/v1/mark`, and `/v1/record/stop`, JSON body `{"take":"takes/<name>","label":"step 2: name"}`. `POST /mcp` on that same port is the MCP HTTP endpoint. A remote Grok connector needs a public tunnel to this process, and the process must stay on the computer that has `DISPLAY`. See [Custom MCP connectors](https://docs.x.ai/grok/connectors) and [tunneling](https://docs.x.ai/grok/connectors/custom-mcp-tunneling).

Read `$ROOT/skills/screenreel/SKILL.md` after install if you need this page on the Linux computer. The skill text is also in the repo at `skills/screenreel/SKILL.md`.

Prefer a display near 1920x1200, the browser full screen, and a steady pace. Optional flags: `--crop x,y,w,h`, `--fps 30`. Type at about 70 ms per key and check the field afterward. Dismiss overlays before recording. Key characters are never logged.

After `record stop`, copy the take folder to the Mac.

## Mac render

The Mac binary is `screenreel` on `PATH`. If it is missing:

```bash
make -C /Users/isaacdonnelson/git/screenreel install
```

Record on the Mac only when the clicks happen on the Mac. `doctor` names the app that needs Screen Recording and Accessibility. That permission sticks to the app that launches the binary.

```bash
screenreel plan --take takes/<name>
screenreel beautify --take takes/<name> --preview
screenreel beautify --take takes/<name>
screenreel frames --take takes/<name> --markers
screenreel frames --take takes/<name> --every 5
screenreel timeline --take takes/<name>
```

`--preview` writes `preview.mp4` at 960x540. The full render writes `out.mp4` at 1920x1080. `timeline.json` has each marker's output `t0` and `t1`.

For a TestFiesta lesson set `style.background` to `#1E1B4B` and `style.radius` to `16` in `edit.json`. Other products pass their own hex. The default background is `#14141C`.

`plan` writes speed segments and cuts of long waits. It keeps `"zooms": "auto"`. A `secret` event becomes a timed blur. `steps` of `{ "label", "target" }` fit a marker's step to a voice length. `blurs` and `covers` take `t0` and `t1` in source seconds. `audio`, `overlays`, `duck`, and `lufs` stay in the file for a later mix. This tool does not mix them.

Open the stills. Check one cursor, readable type, blurred private text, and zooms that still show the result of the click. Change `edit.json` and run `beautify` again when a warning says a zoom is outside the frame or a long still has no actions.

Return the `out.mp4` path and the timeline.

Do not launch Cursorful. Do not add voice, music, captions, or title cards inside this tool.
