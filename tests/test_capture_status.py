"""Tests for get_capture_status / parse_capture_status_output."""

from __future__ import annotations

import json
import re
from pathlib import Path

from perfmon_mcp.tools.capture import (
    _parse_logman_query_text,
    get_capture_status,
    parse_capture_status_output,
)


_JSON_FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def test_get_capture_status_remote_emits_lablink_first_block() -> None:
    out = get_capture_status(target="remote")
    assert "logman query" in out
    assert "```powershell" in out
    match = _JSON_FENCE.search(out)
    assert match, "Remote output must include a JSON sidecar block"
    sidecar = json.loads(match.group(1))
    assert sidecar["parse_with"] == "parse_capture_status_output"
    assert sidecar["shell"] == "powershell"
    # Sidecar primitives only.
    for value in sidecar.values():
        assert isinstance(value, (str, int, float, bool, type(None)))


def test_get_capture_status_local_also_emits_block() -> None:
    """The MCP itself never shells out to logman for state; even
    target='local' yields the same emit-only block so the dispatcher
    can choose where to run it."""
    out = get_capture_status(target="local")
    assert "logman query" in out
    assert _JSON_FENCE.search(out)


def test_get_capture_status_unknown_target() -> None:
    out = get_capture_status(target="bogus")
    assert "Unknown target" in out


def test_parse_logman_query_text_running_shape(fixtures_dir: Path) -> None:
    text = (fixtures_dir / "logman-query-running.txt").read_text(encoding="utf-8")
    parsed = _parse_logman_query_text(text)
    assert parsed["status"].lower() == "running"
    assert "root_path" in parsed
    assert "PerfmonMcpWatch" in parsed["root_path"]


def test_parse_logman_query_text_not_found_shape(fixtures_dir: Path) -> None:
    text = (fixtures_dir / "logman-query-not-found.txt").read_text(encoding="utf-8")
    parsed = _parse_logman_query_text(text)
    assert parsed["status"] == "not_found"


def test_parse_logman_query_text_empty() -> None:
    parsed = _parse_logman_query_text("")
    assert parsed["status"] == "no_output"


def test_parse_capture_status_output_running_renders_table(
    fixtures_dir: Path,
) -> None:
    text = (fixtures_dir / "logman-query-running.txt").read_text(encoding="utf-8")
    out = parse_capture_status_output(text)
    assert "running" in out.lower()
    # Markdown table headers.
    assert "Field" in out
    assert "Value" in out


def test_parse_capture_status_output_not_found_message(
    fixtures_dir: Path,
) -> None:
    text = (fixtures_dir / "logman-query-not-found.txt").read_text(encoding="utf-8")
    out = parse_capture_status_output(text)
    assert "not_found" in out
    assert "No data collector" in out


def test_parse_capture_status_output_empty_text_friendly() -> None:
    out = parse_capture_status_output("")
    assert "No text" in out
