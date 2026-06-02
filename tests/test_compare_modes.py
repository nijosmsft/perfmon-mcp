"""Direct tests for ``compare_logs(mode='counter'|'per_queue'|'per_cpu')``.

The pre-existing ``test_analyze.py::test_compare_logs_two_loads`` only
exercises the default ``mode='counter'`` path. These tests cover the new
v0.2 modes (per_queue / per_cpu), the friendly error for an unknown
mode, and the no-overlap fallback path. The defensive-branch coverage
for ``LogData.counters is None`` lives at the bottom and uses a
hand-built LogData (no mock_relog) so the synthesis is explicit.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from perfmon_mcp.log_state import LogData, register_log
from perfmon_mcp.tools.analyze import compare_logs, load_log


_PERCPU_BASELINE = """(PDH-CSV 4.0) (PST)(480),\\\\HOST\\Processor(Cpu_0)\\% Processor Time,\\\\HOST\\Processor(Cpu_1)\\% Processor Time,\\\\HOST\\Processor(Cpu_2)\\% Processor Time
"01/01/2024 12:00:00.000","10.0","20.0","30.0"
"01/01/2024 12:00:01.000","12.0","22.0","32.0"
"01/01/2024 12:00:02.000","14.0","24.0","34.0"
"""

_PERCPU_TEST = """(PDH-CSV 4.0) (PST)(480),\\\\HOST\\Processor(Cpu_0)\\% Processor Time,\\\\HOST\\Processor(Cpu_1)\\% Processor Time,\\\\HOST\\Processor(Cpu_2)\\% Processor Time
"01/01/2024 12:00:00.000","50.0","60.0","70.0"
"01/01/2024 12:00:01.000","52.0","62.0","72.0"
"01/01/2024 12:00:02.000","54.0","64.0","74.0"
"""

_RQNUM_LOW = """(PDH-CSV 4.0) (PST)(480),\\\\HOST\\Mellanox(RqNum_0)\\Packets/sec,\\\\HOST\\Mellanox(RqNum_1)\\Packets/sec,\\\\HOST\\Mellanox(RqNum_2)\\Packets/sec,\\\\HOST\\Mellanox(RqNum_3)\\Packets/sec
"01/01/2024 12:00:00.000","1000","1100","1200","1300"
"01/01/2024 12:00:01.000","1010","1110","1210","1310"
"""

_RQNUM_HIGH = """(PDH-CSV 4.0) (PST)(480),\\\\HOST\\Mellanox(RqNum_4)\\Packets/sec,\\\\HOST\\Mellanox(RqNum_5)\\Packets/sec,\\\\HOST\\Mellanox(RqNum_6)\\Packets/sec,\\\\HOST\\Mellanox(RqNum_7)\\Packets/sec
"01/01/2024 12:00:00.000","2000","2100","2200","2300"
"01/01/2024 12:00:01.000","2010","2110","2210","2310"
"""

_NETWORK_BYTES = """(PDH-CSV 4.0) (PST)(480),\\\\HOST\\Network Adapter(eth0)\\Bytes Total/sec
"01/01/2024 12:00:00.000","1000000"
"01/01/2024 12:00:01.000","1100000"
"""


def _make_blg(path: Path) -> Path:
    path.write_bytes(b"PERFBINARY-FAKE\x00" * 64)
    return path


def _extract_log_id(text: str) -> str:
    for line in text.splitlines():
        if "`log_id`" in line and "log_" in line:
            for token in line.split("`"):
                if (
                    token.startswith("log_")
                    and len(token) > 4
                    and token != "log_id"
                ):
                    return token
    raise AssertionError(f"No log_id in markdown:\n{text}")


def _load_pair(
    tmp_path: Path,
    mock_relog,
    baseline_csv_text: str,
    test_csv_text: str,
    baseline_name: str = "a.blg",
    test_name: str = "b.blg",
) -> tuple[str, str]:
    """Load two synthetic .blg files via mock_relog and return their log_ids."""
    blg_a = _make_blg(tmp_path / baseline_name)
    blg_b = _make_blg(tmp_path / test_name)
    blg_a.write_bytes(b"A" * 1024)
    blg_b.write_bytes(b"B" * 2048)

    fixture_a = tmp_path / "fixture_a.csv"
    fixture_b = tmp_path / "fixture_b.csv"
    fixture_a.write_text(baseline_csv_text)
    fixture_b.write_text(test_csv_text)

    mock_relog(fixture_a)
    log_a = _extract_log_id(load_log(path=str(blg_a)))
    mock_relog(fixture_b)
    log_b = _extract_log_id(load_log(path=str(blg_b)))
    return log_a, log_b


def test_compare_mode_counter_preserves_v01_behavior(
    tmp_path: Path, mock_relog, fixtures_dir: Path
) -> None:
    blg_a = _make_blg(tmp_path / "a.blg")
    blg_b = _make_blg(tmp_path / "b.blg")
    blg_a.write_bytes(b"A" * 1024)
    blg_b.write_bytes(b"B" * 2048)

    mock_relog(fixtures_dir / "system-overview-relog.csv")
    a = _extract_log_id(load_log(path=str(blg_a)))
    b = _extract_log_id(load_log(path=str(blg_b)))

    out = compare_logs(baseline_log_id=a, test_log_id=b, mode="counter")
    assert "mode=counter" in out
    assert "Counter" in out
    assert "Delta" in out


def test_compare_mode_per_queue_joins_on_kind_index(
    tmp_path: Path, mock_relog, fixtures_dir: Path
) -> None:
    a, b = _load_pair(
        tmp_path, mock_relog,
        Path(fixtures_dir / "mellanox-rss-relog.csv").read_text(),
        Path(fixtures_dir / "mellanox-rss-relog.csv").read_text(),
    )
    out = compare_logs(baseline_log_id=a, test_log_id=b, mode="per_queue")
    assert "mode=per_queue" in out
    # Per-queue output exposes the join key columns.
    assert "Kind" in out
    assert "Index" in out
    assert "BaselineMean" in out
    assert "TestMean" in out


def test_compare_mode_per_cpu_emits_per_cpu_rows(
    tmp_path: Path, mock_relog,
) -> None:
    a, b = _load_pair(tmp_path, mock_relog, _PERCPU_BASELINE, _PERCPU_TEST)
    out = compare_logs(baseline_log_id=a, test_log_id=b, mode="per_cpu")
    assert "mode=per_cpu" in out
    assert "Kind" in out
    assert "Index" in out
    # Each per-CPU row carries the cpu kind tag.
    assert "cpu" in out.lower()


def test_compare_mode_bogus_returns_friendly_error(
    tmp_path: Path, mock_relog, fixtures_dir: Path
) -> None:
    blg_a = _make_blg(tmp_path / "a.blg")
    blg_b = _make_blg(tmp_path / "b.blg")
    blg_a.write_bytes(b"A" * 1024)
    blg_b.write_bytes(b"B" * 2048)

    mock_relog(fixtures_dir / "system-overview-relog.csv")
    a = _extract_log_id(load_log(path=str(blg_a)))
    b = _extract_log_id(load_log(path=str(blg_b)))

    out = compare_logs(baseline_log_id=a, test_log_id=b, mode="bogus")
    lower = out.lower()
    assert "unknown mode" in lower
    assert "bogus" in lower
    for valid in ("counter", "per_queue", "per_cpu"):
        assert valid in lower


def test_compare_mode_per_queue_no_overlapping_counters(
    tmp_path: Path, mock_relog,
) -> None:
    # Both logs are pure summary-style (no RqNum/Cpu instances), so the
    # per_queue join yields no rows.
    a, b = _load_pair(tmp_path, mock_relog, _NETWORK_BYTES, _NETWORK_BYTES)
    out = compare_logs(baseline_log_id=a, test_log_id=b, mode="per_queue")
    assert "No comparable per-queue / per-CPU counters" in out


def test_compare_mode_per_queue_disjoint_indexes_outer_merge(
    tmp_path: Path, mock_relog,
) -> None:
    # Baseline has RqNum 0-3, test has RqNum 4-7 (disjoint indexes for
    # the same CounterName). Outer merge should label every row as
    # either baseline-only or test-only.
    a, b = _load_pair(tmp_path, mock_relog, _RQNUM_LOW, _RQNUM_HIGH)
    out = compare_logs(baseline_log_id=a, test_log_id=b, mode="per_queue")
    assert "mode=per_queue" in out
    assert "baseline-only" in out
    assert "test-only" in out
    assert "Packets/sec" in out


# --- defensive branch coverage: counters=None / summary=None ---------------


def _make_empty_log(blg_path: Path, log_id: str) -> LogData:
    return LogData(
        log_id=log_id,
        blg_path=blg_path,
        export_dir=blg_path.parent,
        dataframes={},
    )


def test_compare_mode_counter_handles_none_counters_and_summary(tmp_path: Path) -> None:
    # Synthesize two LogData rows where both summary AND counters are
    # missing (dataframes={}). The defensive branch in compare_logs
    # previously crashed with "DataFrame truth value is ambiguous"
    # because it used `counters or pd.DataFrame()`. With the fix it
    # should fall through to the empty-delta "no comparable counters"
    # message instead of raising ValueError.
    blg_a = tmp_path / "a.blg"
    blg_b = tmp_path / "b.blg"
    blg_a.write_bytes(b"x")
    blg_b.write_bytes(b"y")
    log_a = _make_empty_log(blg_a, "log_aaaaaaaaaaaa")
    log_b = _make_empty_log(blg_b, "log_bbbbbbbbbbbb")
    register_log(log_a)
    register_log(log_b)

    out = compare_logs(
        baseline_log_id=log_a.log_id,
        test_log_id=log_b.log_id,
        mode="counter",
    )
    assert "No comparable counters" in out


def test_compare_mode_counter_handles_summary_none_with_real_counters(
    tmp_path: Path,
) -> None:
    # summary is None (forced), but counters is a real DataFrame. The
    # fallback recomputes summarize_counters(counters); this exercises
    # the *other* defensive branch that previously hit
    # `summary if not None else summarize_counters(counters or DataFrame())`.
    counters = pd.DataFrame(
        [
            {
                "Timestamp": pd.Timestamp(f"2026-01-01 00:00:{i:02d}"),
                "FullPath": "\\\\H\\O\\C",
                "Hostname": "H",
                "Object": "O",
                "Instance": "",
                "Counter": "C",
                "Value": float(i),
            }
            for i in range(5)
        ]
    )
    blg_a = tmp_path / "a.blg"
    blg_b = tmp_path / "b.blg"
    blg_a.write_bytes(b"x")
    blg_b.write_bytes(b"y")
    log_a = LogData(
        log_id="log_summless0000",
        blg_path=blg_a,
        export_dir=blg_a.parent,
        dataframes={"counters": counters},  # NB: no "summary" key.
    )
    log_b = LogData(
        log_id="log_summless0001",
        blg_path=blg_b,
        export_dir=blg_b.parent,
        dataframes={"counters": counters},
    )
    register_log(log_a)
    register_log(log_b)

    out = compare_logs(
        baseline_log_id=log_a.log_id,
        test_log_id=log_b.log_id,
        mode="counter",
    )
    # The fallback recomputes summaries from counters and produces a
    # non-empty delta table (or an empty one — both are acceptable as
    # long as no ValueError is raised).
    assert "compare_logs" in out or "No comparable counters" in out


def test_compare_unknown_log_id_raises_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        compare_logs(baseline_log_id="log_missing___", test_log_id="log_alsomiss")
