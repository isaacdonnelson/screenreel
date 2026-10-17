#!/usr/bin/env python3
"""stdio MCP server for the Linux screenreel recorder.

Grok Bot command plugin:
  python3 /workspace/screenreel/linux/mcp_server.py

Grok CLI, from this repo:
  grok mcp add screenreel -- python3 linux/mcp_server.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bridge


PROTOCOL = "2024-11-05"
TOOLS = [
    {
        "name": "screenreel_doctor",
        "description": "Check that this Linux display can record. Requires DISPLAY, ffmpeg, xinput, and xdpyinfo. The system cursor is hidden in the capture.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "screenreel_record_start",
        "description": "Start a recording into a take directory. Hides the X cursor and logs real pointer events.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "take": {"type": "string", "description": "Directory for raw.mov and events.jsonl"},
                "crop": {"type": "string", "description": "Optional crop as x,y,w,h in display pixels"},
                "fps": {"type": "integer", "description": "Frames per second. Default 30."},
            },
            "required": ["take"],
            "additionalProperties": False,
        },
    },
    {
        "name": "screenreel_mark",
        "description": "Mark the current step while a take is recording.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "take": {"type": "string"},
                "label": {"type": "string", "description": "Step name, for example step 2: Due Date"},
            },
            "required": ["take", "label"],
            "additionalProperties": False,
        },
    },
    {
        "name": "screenreel_record_stop",
        "description": "Stop the recording and write edit.json. Copy the take to the Mac before plan and beautify.",
        "inputSchema": {
            "type": "object",
            "properties": {"take": {"type": "string"}},
            "required": ["take"],
            "additionalProperties": False,
        },
    },
]


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def read_message():
    header = sys.stdin.readline()
    if header == "":
        return None
    if header.lower().startswith("content-length:"):
        length = int(header.split(":", 1)[1].strip())
        while True:
            line = sys.stdin.readline()
            if line in ("\n", "\r\n", ""):
                break
        return json.loads(sys.stdin.read(length))
    line = header.strip()
    if not line:
        return read_message()
    return json.loads(line)


def tool_result(payload):
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}],
        "isError": not bool(payload.get("ok")),
    }


def call_tool(name, arguments):
    arguments = arguments or {}
    if name == "screenreel_doctor":
        return tool_result(bridge.doctor())
    if name == "screenreel_record_start":
        return tool_result(bridge.record_start(arguments["take"], arguments.get("crop"), arguments.get("fps")))
    if name == "screenreel_mark":
        return tool_result(bridge.mark(arguments["take"], arguments["label"]))
    if name == "screenreel_record_stop":
        return tool_result(bridge.record_stop(arguments["take"]))
    raise KeyError(name)


def handle(message):
    method = message.get("method")
    identity = message.get("id")
    if method == "notifications/initialized":
        return None
    if identity is None:
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": identity,
            "result": {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "screenreel", "version": "0.2.0"},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": identity, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": identity, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            result = call_tool(params.get("name"), params.get("arguments") or {})
        except KeyError:
            return {
                "jsonrpc": "2.0",
                "id": identity,
                "error": {"code": -32602, "message": "Unknown tool"},
            }
        except Exception as error:
            result = tool_result({"ok": False, "error": str(error)})
        return {"jsonrpc": "2.0", "id": identity, "result": result}
    return {
        "jsonrpc": "2.0",
        "id": identity,
        "error": {"code": -32601, "message": "Method not found"},
    }


def main():
    while True:
        message = read_message()
        if message is None:
            return
        response = handle(message)
        if response is not None:
            send(response)


if __name__ == "__main__":
    main()
