"""Snapshot + Get-Counter text parser tests."""

from __future__ import annotations

from pathlib import Path

from perfmon_mcp.parsing.getcounter import parse_get_counter_text
from perfmon_mcp.tools.snapshot import parse_counter_output, snapshot_counters


def test_parse_simple_path_value_pairs():
    text = """
    \\\\HOST\\Processor(_Total)\\% Processor Time : 15.7
    \\\\HOST\\Memory\\Available MBytes : 32768
    """
    df = parse_get_counter_text(text)
    assert not df.empty
    assert {"FullPath", "Value", "Hostname", "Object", "Counter"} <= set(df.columns)
    assert "% Processor Time" in df["Counter"].tolist()


def test_parse_empty_text_returns_empty_frame():
    df = parse_get_counter_text("")
    assert df.empty
    assert "FullPath" in df.columns


def test_parse_fixture_text(fixtures_dir: Path):
    text = (fixtures_dir / "system-overview-getcounter.txt").read_text()
    df = parse_get_counter_text(text)
    assert not df.empty
    # 4 distinct counters from block 1 + repeats from block 2 - dedup by row
    assert df["Counter"].nunique() >= 3


def test_parse_counter_output_tool_returns_markdown(fixtures_dir: Path):
    text = (fixtures_dir / "system-overview-getcounter.txt").read_text()
    out = parse_counter_output(text=text, scenario="system-overview")
    assert isinstance(out, str)
    assert "FullPath" in out or "Counter" in out


def test_snapshot_remote_returns_commands():
    out = snapshot_counters(scenario="system-overview", target="remote")
    assert isinstance(out, str)
    assert "Get-Counter" in out
    # remote MUST NOT silently execute - it must hand back fenced commands
    assert "```" in out


def test_snapshot_unknown_scenario_local():
    out = snapshot_counters(scenario="does-not-exist", target="local")
    assert "unknown" in out.lower() or "not found" in out.lower() or "no profile" in out.lower()
