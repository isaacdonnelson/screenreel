#!/usr/bin/env python3
"""MCP and HTTP front for the Linux screenreel recorder.

Stdio MCP (Grok Build, local Grok Bot):

    python3 linux/screenreel serve

Streamable HTTP MCP, plus a small JSON API (Grok connectors and the xAI API):

    SCREENREEL_TOKEN=secret python3 linux/screenreel serve --http 127.0.0.1:8765

The process must run on the Linux machine whose X display is being recorded.
plan, beautify, frames, and timeline still call the Mac `screenreel` binary
when that binary is on PATH. This file uses only the Python standard library.
"""

import hmac
import json
import os
import shutil
import subprocess
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "screenreel")
PROTOCOL = "2025-03-26"
SERVER_INFO = {"name": "screenreel", "version": "1.0.0"}

MAC_TOOLS = {"plan", "beautify", "frames", "timeline"}

TOOLS = [
    {
        "name": "doctor",
        "description": "Check that this machine can record. Call this before record_start. On Linux it checks DISPLAY, ffmpeg, xinput, and xdpyinfo.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "record_start",
        "description": "Start recording the X display into a take folder. The system cursor is hidden. Clicks and key timing are written to events.jsonl. Run this on the computer that will click the product.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "take": {"type": "string", "description": "Take directory, for example takes/demo."},
                "crop": {"type": "string", "description": "Optional capture rectangle as x,y,width,height."},
                "fps": {"type": "integer", "description": "Frames per second. Default 30."},
            },
            "required": ["take"],
            "additionalProperties": False,
        },
    },
    {
        "name": "mark",
        "description": "Label the current moment of a recording that is already running.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "take": {"type": "string", "description": "Take directory passed to record_start."},
                "label": {"type": "string", "description": "Step name, for example 'step 2: open settings'."},
            },
            "required": ["take", "label"],
            "additionalProperties": False,
        },
    },
    {
        "name": "record_stop",
        "description": "Stop the recording and write edit.json. The raw file is raw.mov in the take folder. Copy the folder to the Mac before plan or beautify.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "take": {"type": "string", "description": "Take directory passed to record_start."},
            },
            "required": ["take"],
            "additionalProperties": False,
        },
    },
    {
        "name": "plan",
        "description": "On the Mac binary, write speed segments and cuts into edit.json. Linux recording does not plan.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "take": {"type": "string", "description": "Take directory copied to the Mac."},
            },
            "required": ["take"],
            "additionalProperties": False,
        },
    },
    {
        "name": "beautify",
        "description": "On the Mac binary, render the take. Pass preview true for a 960x540 preview.mp4. The full render writes out.mp4.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "take": {"type": "string", "description": "Take directory on the Mac."},
                "preview": {"type": "boolean", "description": "Write preview.mp4 instead of the full out.mp4."},
            },
            "required": ["take"],
            "additionalProperties": False,
        },
    },
    {
        "name": "frames",
        "description": "On the Mac binary, write stills from the rendered video so the picture can be checked.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "take": {"type": "string", "description": "Take directory on the Mac."},
                "markers": {"type": "boolean", "description": "Write a still at each marker."},
                "every": {"type": "number", "description": "Write a still every N seconds."},
            },
            "required": ["take"],
            "additionalProperties": False,
        },
    },
    {
        "name": "timeline",
        "description": "On the Mac binary, write timeline.json with each marker's output t0 and t1.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "take": {"type": "string", "description": "Take directory on the Mac."},
            },
            "required": ["take"],
            "additionalProperties": False,
        },
    },
]


def tool_map():
    return {tool["name"]: tool for tool in TOOLS}


def function_tools():
    """Tool list for the xAI Responses API (`type: function`)."""
    listed = []
    for tool in TOOLS:
        listed.append({
            "type": "function",
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["inputSchema"],
        })
    return listed


def is_python_script(path):
    try:
        with open(path, "rb") as handle:
            head = handle.read(80)
    except OSError:
        return False
    return head.startswith(b"#!") and b"python" in head


def resolve_binary(name):
    override = os.environ.get("SCREENREEL_BIN")
    if override:
        return override
    found = shutil.which("screenreel")
    if name in MAC_TOOLS:
        if found and not is_python_script(found):
            return found
        return None
    if sys.platform.startswith("linux") and os.path.isfile(CLI):
        return CLI
    if found:
        return found
    if os.path.isfile(CLI):
        return CLI
    return None


def command_args(name, arguments):
    take = arguments.get("take")
    if name == "doctor":
        return ["doctor"]
    if name == "record_start":
        argv = ["record", "start", "--take", take]
        if arguments.get("crop"):
            argv.extend(["--crop", str(arguments["crop"])])
        if arguments.get("fps"):
            argv.extend(["--fps", str(int(arguments["fps"]))])
        return argv
    if name == "mark":
        return ["mark", "--take", take, "--label", str(arguments["label"])]
    if name == "record_stop":
        return ["record", "stop", "--take", take]
    if name == "plan":
        return ["plan", "--take", take]
    if name == "beautify":
        argv = ["beautify", "--take", take]
        if arguments.get("preview"):
            argv.append("--preview")
        return argv
    if name == "frames":
        argv = ["frames", "--take", take]
        if arguments.get("markers"):
            argv.append("--markers")
        if arguments.get("every") is not None:
            argv.extend(["--every", str(arguments["every"])])
        return argv
    if name == "timeline":
        return ["timeline", "--take", take]
    return None


def call_tool(name, arguments):
    if not isinstance(arguments, dict):
        return {"ok": False, "error": "Tool arguments must be an object."}
    spec = tool_map().get(name)
    if spec is None:
        return {"ok": False, "error": "Unknown tool %s." % name}
    missing = [key for key in spec["inputSchema"].get("required", []) if arguments.get(key) in (None, "")]
    if missing:
        return {"ok": False, "error": "Missing " + ", ".join(missing) + "."}
    binary = resolve_binary(name)
    if binary is None:
        return {
            "ok": False,
            "error": "The Mac screenreel binary is not on PATH. Copy the take to the Mac and run plan, beautify, frames, or timeline there.",
        }
    if is_python_script(binary):
        argv = [sys.executable, binary]
    else:
        argv = [binary]
    argv.extend(command_args(name, arguments))
    timeout = int(os.environ.get("SCREENREEL_TIMEOUT", "600"))
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "screenreel timed out."}
    except OSError as error:
        return {"ok": False, "error": str(error)}
    payload = _parse_stdout(completed.stdout)
    if payload is None:
        return {
            "ok": False,
            "error": (completed.stderr or completed.stdout or "screenreel returned no JSON.").strip(),
            "exit": completed.returncode,
        }
    return payload


def _parse_stdout(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    if isinstance(value, dict):
        return value
    return None


def rpc_handle(message, session):
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid JSON-RPC.")
    method = message.get("method")
    request_id = message.get("id")
    if method is None:
        return None
    if request_id is None and method.startswith("notifications/"):
        return None
    params = message.get("params") or {}
    if method == "initialize":
        version = params.get("protocolVersion") or PROTOCOL
        if version not in (PROTOCOL, "2024-11-05"):
            version = PROTOCOL
        session["initialized"] = True
        return _result(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        })
    if method == "ping":
        return _result(request_id, {})
    if not session.get("initialized"):
        return _error(request_id, -32600, "Send initialize first.")
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        payload = call_tool(name, arguments)
        text = json.dumps(payload, sort_keys=True)
        return _result(request_id, {
            "content": [{"type": "text", "text": text}],
            "isError": not bool(payload.get("ok")),
        })
    return _error(request_id, -32601, "Unknown method %s." % method)


def _result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def stdio_main():
    session = {"initialized": False}
    raw = sys.stdin.buffer
    while True:
        first = raw.readline()
        if not first:
            return
        if not first.strip():
            continue
        if first.lower().startswith(b"content-length:"):
            length = int(first.split(b":", 1)[1].strip())
            while True:
                extra = raw.readline()
                if extra in (b"\r\n", b"\n", b""):
                    break
            body = raw.read(length)
        else:
            body = first
        try:
            message = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _write_stdout(_error(None, -32700, "Parse error."))
            continue
        response = rpc_handle(message, session)
        if response is not None:
            _write_stdout(response)


def _write_stdout(payload):
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


class Handler(BaseHTTPRequestHandler):
    server_version = "screenreel/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s\n" % (fmt % args))

    def do_GET(self):
        if not self._authorized():
            return
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._json(200, {"ok": True, "server": SERVER_INFO, "platform": sys.platform})
            return
        if path == "/v1/tools":
            self._json(200, {"tools": function_tools()})
            return
        if path == "/mcp":
            self._empty(405, {"Allow": "POST, DELETE"})
            return
        self._json(404, {"ok": False, "error": "Not found."})

    def do_POST(self):
        if not self._authorized():
            return
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"ok": False, "error": "Body must be JSON."})
            return
        if path == "/v1/call":
            name = body.get("name") or body.get("tool")
            arguments = body.get("arguments") or {}
            payload = call_tool(name, arguments)
            self._json(200, payload)
            return
        if path == "/mcp":
            self._mcp(body)
            return
        self._json(404, {"ok": False, "error": "Not found."})

    def do_DELETE(self):
        if not self._authorized():
            return
        if self.path.split("?", 1)[0] != "/mcp":
            self._json(404, {"ok": False, "error": "Not found."})
            return
        session_id = self.headers.get("Mcp-Session-Id")
        if session_id and session_id in self.server.sessions:
            del self.server.sessions[session_id]
            self._empty(204, {})
            return
        self._empty(404, {})

    def _mcp(self, body):
        session_id = self.headers.get("Mcp-Session-Id")
        method = body.get("method")
        if method == "initialize":
            session_id = str(uuid.uuid4())
            session = {"initialized": False}
            self.server.sessions[session_id] = session
            response = rpc_handle(body, session)
            self._rpc(200, response, {"Mcp-Session-Id": session_id})
            return
        session = self.server.sessions.get(session_id) if session_id else None
        if session is None:
            self._json(404, {"ok": False, "error": "Unknown or missing Mcp-Session-Id."})
            return
        response = rpc_handle(body, session)
        if response is None:
            self._empty(202, {})
            return
        self._rpc(200, response, {})

    def _authorized(self):
        token = os.environ.get("SCREENREEL_TOKEN")
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        expected = "Bearer " + token
        if len(header) == len(expected) and hmac.compare_digest(header, expected):
            return True
        self._json(401, {"ok": False, "error": "Unauthorized."})
        return False

    def _rpc(self, status, payload, extra):
        accept = self.headers.get("Accept", "")
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if "text/event-stream" in accept:
            body = ("event: message\ndata: %s\n\n" % data.decode("utf-8")).encode("utf-8")
            headers = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
        else:
            body = data
            headers = {"Content-Type": "application/json"}
        headers.update(extra)
        self._send(status, body, headers)

    def _json(self, status, payload):
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self._send(status, body, {"Content-Type": "application/json"})

    def _empty(self, status, headers):
        self._send(status, b"", headers)

    def _send(self, status, body, headers):
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


def http_main(address):
    host, port = address
    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("SCREENREEL_TOKEN"):
        sys.stderr.write("Set SCREENREEL_TOKEN before listening on a non-loopback address.\n")
        raise SystemExit(1)
    server = ThreadingHTTPServer((host, port), Handler)
    server.sessions = {}
    sys.stderr.write("screenreel listening on http://%s:%s\n" % (host, port))
    server.serve_forever()


def parse_http(value):
    if value is None or value == "":
        return "127.0.0.1", 8765
    if value.startswith("["):
        host, _, port = value[1:].partition("]")
        port = port[1:] if port.startswith(":") else "8765"
        return host, int(port)
    if ":" in value:
        host, port = value.rsplit(":", 1)
        return host or "127.0.0.1", int(port)
    return "127.0.0.1", int(value)


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if "--http" in argv:
        index = argv.index("--http")
        value = argv[index + 1] if index + 1 < len(argv) and not argv[index + 1].startswith("--") else ""
        http_main(parse_http(value))
        return
    stdio_main()


if __name__ == "__main__":
    main()
