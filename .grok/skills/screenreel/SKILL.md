---
name: screenreel
description: >
  Record a product on the Linux display that can click it, then render the
  demo on the Mac. Use when asked to screen record, record a demo, beautify
  a screen recording, or make a product walkthrough.
when-to-use: screen record, demo video, product walkthrough, beautify a recording
user-invocable: true
---

# Screenreel

Record on the computer that can click the product. On Linux that is the X display (`DISPLAY` is set). Render on the Mac. The take folder is the same in both places.

Prefer the screenreel tools when they are connected. The tool names are `doctor`, `record_start`, `mark`, `record_stop`, `plan`, `beautify`, `frames`, and `timeline`. `plan`, `beautify`, `frames`, and `timeline` need the Mac binary on `PATH`. If those tools are not connected, run the same steps with the shell.

Ask for the product URL, the click path, and a background hex before recording. The default background is `#14141C`. For a TestFiesta lesson use `#1E1B4B` and corner radius 16.

## Linux, where the clicks happen

Grok Bot's cloud computer and the local computer are different machines. Record on the one that shows the product.

Download is the `linux/` directory. Python 3 is enough. No install step.

```bash
python3 linux/screenreel doctor
python3 linux/screenreel record start --take takes/<name>
python3 linux/screenreel mark --take takes/<name> --label "step 2: name"
python3 linux/screenreel record stop --take takes/<name>
```

`doctor` must succeed before `record_start`. Optional flags: `--crop x,y,w,h`, `--fps 30`. Every command prints one JSON object. A non-zero exit means `"ok": false`.

The capture hides the X cursor. Key characters are never logged. Copy the take folder to the Mac after the raw file is closed.

## MCP

In this repo, Grok Build starts the stdio server from `.grok/config.toml`. The same command is how a local Grok Bot shell attaches it:

```bash
python3 linux/screenreel serve
```

For Grok on the web, or the xAI API, listen on the recording machine and tunnel that port. A public bind requires `SCREENREEL_TOKEN`. Loopback does not.

```bash
SCREENREEL_TOKEN=secret python3 linux/screenreel serve --http 127.0.0.1:8765
```

The MCP URL is `http://127.0.0.1:8765/mcp`. In Grok, add a custom connector with that public URL and the bearer token. Streamable HTTP is the transport. Cloudflare quick tunnels can carry it. An SSE-only tunnel cannot.

## API

Point the Responses API at the same MCP URL:

```json
{
  "type": "mcp",
  "server_url": "https://<tunnel>/mcp",
  "server_label": "screenreel",
  "server_description": "Record the Linux display that is showing the product.",
  "authorization": "<SCREENREEL_TOKEN>"
}
```

To run the tools yourself, `GET /v1/tools` returns Responses API function definitions. `POST /v1/call` with `{"name":"doctor","arguments":{}}` runs one. Send the JSON result back as the function output. Built-in xAI tools run on xAI servers. These functions run only on the machine that is serving them.

## Mac, where the picture is rendered

```bash
screenreel plan --take takes/<name>
screenreel beautify --take takes/<name> --preview
screenreel beautify --take takes/<name>
screenreel frames --take takes/<name> --markers
screenreel timeline --take takes/<name>
```

`--preview` writes `preview.mp4` at 960x540. The full render writes `out.mp4`, normally 1920x1080. `timeline.json` has each marker's output `t0` and `t1`.

`edit.json` holds the background, zooms, blurs, covers, speed segments, and cuts. Change that file and run `beautify` again when a zoom, a blur, or a wait is wrong.

Return the `out.mp4` path and the timeline. Do not launch Cursorful. Do not add voice, music, captions, or title cards inside this tool.
