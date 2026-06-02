"""Direct tests for the ``analyze`` mega-tool surface.

These tests exercise the section selector, the unknown-section friendly
error, and the empty-per-CPU degrade path explicitly. The pre-existing
``test_analyze.py`` covers the underlying aggregator and the
``load_blg`` round-trip but never invokes ``analyze`` itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from perfmon_mcp.tools.analyze import analyze, load_log


def _extract_log_id(markdown: str) -> str:
    for line in markdown.splitlines():
        if "`log_id`" in line and "log_" in line:
            for token in line.split("`"):
                if (
                    token.startswith("log_")
                    and len(token) > 4
                    and token != "log_id"
                ):
                    return token
    raise AssertionError(f"No log_id in markdown:\n{markdown}")


def _load(mock_relog, fixtures_dir: Path, fake_blg: Path, fixture_name: str) -> str:
    mock_relog(fixtures_dir / fixture_name)
    return _extract_log_id(load_log(path=str(fake_blg)))


def test_analyze_all_sections_returns_every_header(
    mock_relog, fixtures_dir: Path, fake_blg: Path
) -> None:
    log_id = _load(mock_relog, fixtures_dir, fake_blg, "mellanox-rss-relog.csv")
    out = analyze(log_id=log_id)
    assert "# Analyze:" in out
    assert "## Overview" in out
    assert "## Top counters by mean" in out
    assert "## Per-queue / per-CPU rollup" in out
    assert "## Warnings" in out


def test_analyze_single_section_emits_only_overview(
    mock_relog, fixtures_dir: Path, fake_blg: Path
) -> None:
    log_id = _load(mock_relog, fixtures_dir, fake_blg, "system-overview-relog.csv")
    out = analyze(log_id=log_id, sections="overview")
    assert "## Overview" in out
    # No other sections should appear.
    assert "## Top counters by mean" not in out
    assert "## Per-queue / per-CPU rollup" not in out
    assert "## Warnings" not in out


def test_analyze_two_sections_preserves_request_order(
    mock_relog, fixtures_dir: Path, fake_blg: Path
) -> None:
    log_id = _load(mock_relog, fixtures_dir, fake_blg, "mellanox-rss-relog.csv")
    out = analyze(log_id=log_id, sections="overview,per_queue")
    assert "## Overview" in out
    assert "## Per-queue / per-CPU rollup" in out
    assert "## Top counters by mean" not in out
    assert "## Warnings" not in out
    # Order matters: overview header appears before per-queue header.
    assert out.index("## Overview") < out.index("## Per-queue / per-CPU rollup")


def test_analyze_unknown_section_returns_friendly_error(
    mock_relog, fixtures_dir: Path, fake_blg: Path
) -> None:
    log_id = _load(mock_relog, fixtures_dir, fake_blg, "system-overview-relog.csv")
    out = analyze(log_id=log_id, sections="bogus")
    lower = out.lower()
    assert "unknown sections" in lower
    assert "bogus" in lower
    # All four valid section names should appear in the error message.
    for valid in ("overview", "top_counters", "per_queue", "warnings"):
        assert valid in lower


def test_analyze_unknown_log_id_raises_value_error() -> None:
    with pytest.raises(ValueError):
        analyze(log_id="log_does_not_exist")


def test_analyze_with_no_per_queue_data_still_succeeds(
    mock_relog, fixtures_dir: Path, fake_blg: Path
) -> None:
    # system-overview fixture has no RqNum / SqNum / Queue / Cpu instances.
    log_id = _load(mock_relog, fixtures_dir, fake_blg, "system-overview-relog.csv")
    out = analyze(log_id=log_id)
    assert "## Per-queue / per-CPU rollup" in out
    assert "No per-queue / per-CPU counters present" in out
