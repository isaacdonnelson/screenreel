#!/usr/bin/env python3
"""Checks the stdio MCP server and the local HTTP API. No display required."""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCREENREEL = os.path.join(HERE, "screenreel")


def fail(message):
    sys.stderr.write(message + "\n")
    raise SystemExit(1)


def rpc(process, message):
    payload = json.dumps(message).encode("utf-8") + b"\n"
    process.stdin.write(payload)
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        fail("stdio server closed stdout")
    return json.loads(line)


def check_stdio():
    process = subprocess.Popen(
        [sys.executable, SCREENREEL, "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=ROOT,
    )
    try:
        started = rpc(process, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "check", "version": "0"}},
        })
        if started["result"]["serverInfo"]["name"] != "screenreel":
            fail("initialize did not name screenreel: %s" % started)
        process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        process.stdin.flush()
        listed = rpc(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [tool["name"] for tool in listed["result"]["tools"]]
        for expected in ("doctor", "record_start", "mark", "record_stop", "plan", "beautify"):
            if expected not in names:
                fail("missing tool %s" % expected)
        called = rpc(process, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "doctor", "arguments": {}},
        })
        body = json.loads(called["result"]["content"][0]["text"])
        if "ok" not in body or "platform" not in body:
            fail("doctor result was not the CLI payload: %s" % body)
        missing = rpc(process, {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "record_start", "arguments": {}},
        })
        if missing["result"]["isError"] is not True:
            fail("record_start without a take should be an error")
        framed = subprocess.run(
            [sys.executable, SCREENREEL, "serve"],
            input=_framed({"jsonrpc": "2.0", "id": 7, "method": "ping"}),
            capture_output=True,
            cwd=ROOT,
        )
        if b'"id":7' not in framed.stdout and b'"id": 7' not in framed.stdout:
            fail("content-length framing was not accepted: %s" % framed.stdout)
    finally:
        process.kill()
        process.wait(timeout=5)


def _framed(message):
    body = json.dumps(message).encode("utf-8")
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


def check_http():
    env = os.environ.copy()
    env["SCREENREEL_TOKEN"] = "test-token"
    process = subprocess.Popen(
        [sys.executable, SCREENREEL, "serve", "--http", "127.0.0.1:8765"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=ROOT,
        env=env,
    )
    try:
        _wait_until_up(process)
        refused = urllib.request.Request("http://127.0.0.1:8765/health")
        try:
            urllib.request.urlopen(refused, timeout=2)
            fail("missing token should be rejected")
        except urllib.error.HTTPError as error:
            if error.code != 401:
                fail("expected 401, got %s" % error.code)
        health = _get("/health")
        if not health["ok"]:
            fail("health was not ok")
        tools = _get("/v1/tools")
        doctor = next(tool for tool in tools["tools"] if tool["name"] == "doctor")
        if doctor["type"] != "function" or doctor["parameters"]["type"] != "object":
            fail("function tool shape is wrong: %s" % doctor)
        called = _post("/v1/call", {"name": "doctor", "arguments": {}})
        if "platform" not in called:
            fail("HTTP doctor did not return the CLI payload")
        session = _mcp({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}},
        })
        if not session["id"]:
            fail("MCP initialize did not return a session")
        listed = _mcp({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        }, session["id"], accept="application/json")
        if listed["body"]["result"]["tools"][0]["name"] != "doctor":
            fail("HTTP tools/list failed: %s" % listed)
        noted = _mcp({"jsonrpc": "2.0", "method": "notifications/initialized"}, session["id"])
        if noted["status"] != 202:
            fail("notification should be 202, got %s" % noted["status"])
    finally:
        process.kill()
        process.wait(timeout=5)


def _wait_until_up(process):
    for _ in range(50):
        if process.poll() is not None:
            fail("HTTP server exited: %s" % process.stderr.read().decode())
        try:
            request = urllib.request.Request(
                "http://127.0.0.1:8765/health",
                headers={"Authorization": "Bearer test-token"},
            )
            urllib.request.urlopen(request, timeout=0.2)
            return
        except OSError:
            import time
            time.sleep(0.05)
    fail("HTTP server did not start")


def _get(path):
    request = urllib.request.Request(
        "http://127.0.0.1:8765" + path,
        headers={"Authorization": "Bearer test-token"},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.loads(response.read().decode())


def _post(path, payload):
    data = json.dumps(payload).encode()
    request = urllib.request.Request(
        "http://127.0.0.1:8765" + path,
        data=data,
        headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode())


def _mcp(message, session_id=None, accept="application/json, text/event-stream"):
    data = json.dumps(message).encode()
    headers = {
        "Authorization": "Bearer test-token",
        "Content-Type": "application/json",
        "Accept": accept,
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = urllib.request.Request("http://127.0.0.1:8765/mcp", data=data, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    raw = response.read()
    body = None
    if raw:
        text = raw.decode()
        if text.startswith("event:"):
            data_line = next(line for line in text.splitlines() if line.startswith("data:"))
            body = json.loads(data_line[len("data:"):].strip())
        else:
            body = json.loads(text)
    return {
        "status": response.status,
        "id": response.headers.get("Mcp-Session-Id"),
        "body": body,
    }


def check_public_bind_needs_token():
    env = os.environ.copy()
    env.pop("SCREENREEL_TOKEN", None)
    try:
        completed = subprocess.run(
            [sys.executable, SCREENREEL, "serve", "--http", "0.0.0.0:8766"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=env,
            timeout=3,
        )
    except subprocess.TimeoutExpired:
        fail("public bind without a token should exit")
    if completed.returncode == 0:
        fail("public bind without a token should fail")
    if "SCREENREEL_TOKEN" not in completed.stderr:
        fail("public bind error should name SCREENREEL_TOKEN")


if __name__ == "__main__":
    check_stdio()
    check_http()
    check_public_bind_needs_token()
    print("ok")
