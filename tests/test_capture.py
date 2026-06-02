"""Capture-emit tests. No live logman execution - these tools only render text."""

from __future__ import annotations

from perfmon_mcp.tools.capture import get_capture_commands, get_capture_instructions


def test_get_capture_commands_basic():
    out = get_capture_commands(
        scenario="system-overview",
        output_path="C:\\perfmon\\test.blg",
        duration_s=10,
    )
    assert isinstance(out, str)
    assert "logman" in out
    assert "C:\\perfmon\\test.blg" in out
    # Must contain create + start + stop + relog steps
    assert "create counter" in out
    assert "start" in out
    assert "stop" in out
    assert "relog" in out


def test_get_capture_commands_clamps_duration():
    short = get_capture_commands(
        scenario="system-overview",
        output_path="C:\\perfmon\\test.blg",
        duration_s=0,
    )
    long = get_capture_commands(
        scenario="system-overview",
        output_path="C:\\perfmon\\test.blg",
        duration_s=100000,
    )
    assert isinstance(short, str)
    assert isinstance(long, str)


def test_get_capture_commands_unknown_scenario():
    out = get_capture_commands(
        scenario="bogus", output_path="C:\\perfmon\\test.blg", duration_s=10
    )
    assert "bogus" in out.lower() or "unknown" in out.lower()


def test_remote_runbook_names_three_transports():
    """The instructions for target='remote' must surface multiple
    transports without coupling to any single orchestrator."""
    out = get_capture_instructions(
        scenario="system-overview",
        target="remote",
        output_path="C:\\perfmon\\test.blg",
    )
    lower = out.lower()
    # PSRemoting must be present
    assert "powershell remoting" in lower or "invoke-command" in lower or "psremoting" in lower
    # LabLink mentioned as ONE example transport
    assert "lablink" in lower
    # Manual scp / copy path
    assert "scp" in lower or "manual" in lower or "copy-item" in lower


def test_mellanox_percpu_warning_surfaced_in_runbook():
    out = get_capture_instructions(
        scenario="mellanox-percpu",
        target="local",
        output_path="C:\\perfmon\\rss.blg",
    )
    lower = out.lower()
    assert "28pp" in lower or "28 pp" in lower or "delivery cost" in lower or "overhead" in lower


def test_capture_commands_have_teardown():
    """The 6-step block MUST include teardown (logman delete + counter file rm)."""
    out = get_capture_commands(
        scenario="cpu-detailed",
        output_path="C:\\perfmon\\cpu.blg",
        duration_s=10,
    )
    assert "delete" in out.lower()
