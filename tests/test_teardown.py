"""Teardown-emit tests. No live logman/Stop-Process - emit-only contract."""

from __future__ import annotations

import json
import re

from perfmon_mcp.tools.capture import (
    get_teardown_commands,
    parse_teardown_output,
)

_JSON_FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def test_get_teardown_commands_default_collector_name():
    out = get_teardown_commands()
    assert "PerfmonMcpWatch" in out
    assert "logman stop" in out
    assert "logman delete" in out
    assert "Stop-Process" in out
    assert "perfmon, typeperf, relog, logman" in out


def test_get_teardown_commands_remote_target_has_sidecar():
    out = get_teardown_commands(collector_name="CustomCollector", target="remote")
    assert "CustomCollector" in out
    match = _JSON_FENCE.search(out)
    assert match, "Remote output must include a JSON sidecar block"
    sidecar = json.loads(match.group(1))
    assert sidecar["parse_with"] == "parse_teardown_output"
    assert sidecar["shell"] == "powershell"
    assert isinstance(sidecar.get("timeout_s"), (int, float))


def test_get_teardown_commands_rejects_empty_collector():
    out = get_teardown_commands(collector_name="")
    assert "non-empty" in out


def test_get_teardown_commands_unknown_target():
    out = get_teardown_commands(target="bogus")
    assert "Unknown target" in out


def test_parse_teardown_output_empty_means_clean():
    out = parse_teardown_output("")
    assert "PerfmonMcpWatch" in out
    assert "Teardown complete" in out or "no output" in out


def test_parse_teardown_output_still_present():
    out = parse_teardown_output(
        "Data Collector Set                Status\nPerfmonMcpWatch                   Running",
        collector_name="PerfmonMcpWatch",
    )
    assert "still mentions" in out or "rerun" in out.lower()
    assert "PerfmonMcpWatch" in out


def test_get_teardown_commands_escapes_single_quotes():
    out = get_teardown_commands(collector_name="It's-A-Test")
    assert "It''s-A-Test" in out
