#!/usr/bin/env python3
"""Local HTTP API and MCP endpoint for the Linux recorder.

  python3 linux/api_server.py
  curl -s localhost:8765/v1/doctor

POST /mcp speaks MCP over HTTP so a Grok custom connector can use a tunnel
whose public URL forwards to this process. The process must run on the
computer that owns DISPLAY, because that is where the recording happens.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bridge


HOST = "127.0.0.1"
PORT = 8765
SESSION = "screenreel"


def tool_result(payload):
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}],
        "isError": not bool(payload.get("ok")),
    }


def dispatch(name, arguments):
    arguments = arguments or {}
    if name == "screenreel_doctor":
        return bridge.doctor()
    if name == "screenreel_record_start":
        return bridge.record_start(arguments.get("take", ""), arguments.get("crop"), arguments.get("fps"))
    if name == "screenreel_mark":
        return bridge.mark(arguments.get("take", ""), arguments.get("label", ""))
    if name == "screenreel_record_stop":
        return bridge.record_stop(arguments.get("take", ""))
    return {"ok": False, "error": "Unknown tool " + str(name)}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/v1/health"):
            self._json(200, {
                "ok": True,
                "service": "screenreel",
                "mcp": "/mcp",
                "routes": ["GET /v1/health", "POST /v1/doctor", "POST /v1/record/start", "POST /v1/mark", "POST /v1/record/stop", "POST /mcp"],
            })
            return
        if path == "/v1/doctor":
            self._json(200, bridge.doctor())
            return
        if path == "/mcp":
            self._json(405, {"jsonrpc": "2.0", "error": {"code": -32000, "message": "POST JSON-RPC to /mcp"}, "id": None})
            return
        self._json(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        body = self._body()
        if path == "/mcp":
            self._mcp(body)
            return
        if path == "/v1/doctor":
            self._json(200, bridge.doctor())
            return
        if path == "/v1/record/start":
            self._json(200, dispatch("screenreel_record_start", body))
            return
        if path == "/v1/mark":
            self._json(200, dispatch("screenreel_mark", body))
            return
        if path == "/v1/record/stop":
            self._json(200, dispatch("screenreel_record_stop", body))
            return
        self._json(404, {"ok": False, "error": "Not found"})

    def _mcp(self, body):
        method = body.get("method")
        identity = body.get("id")
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "screenreel", "version": "0.2.0"},
            }
            self._json(200, {"jsonrpc": "2.0", "id": identity, "result": result}, session=True)
            return
        if method == "ping":
            self._json(200, {"jsonrpc": "2.0", "id": identity, "result": {}})
            return
        if method == "tools/list":
            self._json(200, {"jsonrpc": "2.0", "id": identity, "result": {"tools": __import__("mcp_server").TOOLS}})
            return
        if method == "tools/call":
            params = body.get("params") or {}
            payload = dispatch(params.get("name"), params.get("arguments") or {})
            self._json(200, {"jsonrpc": "2.0", "id": identity, "result": tool_result(payload)})
            return
        if method == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._json(200, {"jsonrpc": "2.0", "id": identity, "error": {"code": -32601, "message": "Method not found"}})

    def _body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _json(self, status, payload, session=False):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if session:
            self.send_header("Mcp-Session-Id", SESSION)
        self.end_headers()
        self.wfile.write(data)


def main():
    host = HOST
    port = PORT
    args = sys.argv[1:]
    if "--host" in args:
        host = args[args.index("--host") + 1]
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    server = ThreadingHTTPServer((host, port), Handler)
    sys.stderr.write("screenreel api http://%s:%s\n" % (host, port))
    server.serve_forever()


if __name__ == "__main__":
    main()
