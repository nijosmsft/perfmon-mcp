"""Real MCP stdio handshake test (regression guard for #38).

Spawns ``python -m perfmon_mcp.server`` and drives a JSON-RPC
``initialize`` + ``tools/list`` over stdio to prove the server starts and
speaks the protocol with no ``ModuleNotFoundError`` from the FastMCP
migration (``mcp.server.fastmcp`` -> standalone ``fastmcp`` package).
"""

from __future__ import annotations

import json
import subprocess
import sys


def _send(proc: subprocess.Popen, obj: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(obj) + "\n").encode())
    proc.stdin.flush()


def _read_json(proc: subprocess.Popen) -> dict:
    assert proc.stdout is not None
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("server closed stdout before responding")
        line = line.strip()
        if not line:
            continue
        return json.loads(line)


def test_stdio_initialize_and_tools_list():
    proc = subprocess.Popen(
        [sys.executable, "-m", "perfmon_mcp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest-probe", "version": "1.0"},
            },
        })
        init = _read_json(proc)
        assert init.get("id") == 1, init
        result = init.get("result")
        assert result is not None, f"no result in initialize response: {init}"
        assert result["serverInfo"]["name"] == "perfmon-mcp"
        assert "tools" in result["capabilities"]

        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools_resp = _read_json(proc)
        assert tools_resp.get("id") == 2, tools_resp
        names = {t["name"] for t in tools_resp["result"]["tools"]}
        for expected in ("snapshot_counters", "get_capture_commands",
                         "load_log", "analyze"):
            assert expected in names, f"missing tool {expected}: {sorted(names)}"
    finally:
        if proc.stdin:
            proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        assert "ModuleNotFoundError" not in err, err
