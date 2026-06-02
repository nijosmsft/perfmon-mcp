"""Aggregator + load_blg analyze tests. Uses mock_relog fixture."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from perfmon_mcp.parsing.aggregator import (
    bucket_timeline,
    compare_summaries,
    extract_queue_index,
    normalize_counter_name,
    per_queue_aggregate,
    split_counter_path,
    summarize_counters,
)
from perfmon_mcp.tools.analyze import (
    compare_logs,
    get_counter_summary,
    get_counter_timeline,
    get_per_queue_summary,
    list_loaded_logs,
    load_blg,
)


# --- pure aggregator tests --------------------------------------------------


def test_split_counter_path_basic():
    host, obj, inst, counter = split_counter_path(
        "\\\\HOST\\Processor(_Total)\\% Processor Time"
    )
    assert host == "HOST"
    assert obj == "Processor"
    assert inst == "_Total"
    assert counter == "% Processor Time"


def test_split_counter_path_no_instance():
    host, obj, inst, counter = split_counter_path(
        "\\\\HOST\\Memory\\Available MBytes"
    )
    assert host == "HOST"
    assert obj == "Memory"
    assert inst == ""
    assert counter == "Available MBytes"


def test_normalize_strips_host_prefix():
    a = normalize_counter_name("\\\\HOST1\\Processor(0)\\% Processor Time")
    b = normalize_counter_name("\\\\HOST2\\Processor(0)\\% Processor Time")
    assert a == b


def test_extract_queue_index_recognizes_all_kinds():
    assert extract_queue_index("RqNum_5") == ("rqnum", 5)
    assert extract_queue_index("SqNum_12") == ("sqnum", 12)
    assert extract_queue_index("Queue_3") == ("queue", 3)
    assert extract_queue_index("Cpu_27") == ("cpu", 27)
    assert extract_queue_index("_Total") is None
    assert extract_queue_index("") is None


def test_summarize_counters_produces_percentiles():
    df = pd.DataFrame(
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
            for i in range(10)
        ]
    )
    out = summarize_counters(df)
    assert not out.empty
    row = out.iloc[0]
    assert row["Samples"] == 10
    assert row["Min"] == 0
    assert row["Max"] == 9
    assert "P95" in out.columns


def test_bucket_timeline_filters_and_buckets():
    df = pd.DataFrame(
        [
            {
                "Timestamp": pd.Timestamp("2026-01-01 00:00:00") + pd.Timedelta(seconds=i),
                "FullPath": f"\\\\H\\O\\{name}",
                "Hostname": "H",
                "Object": "O",
                "Instance": "",
                "Counter": name,
                "Value": float(i),
            }
            for i in range(6)
            for name in ("MatchMe", "Ignore")
        ]
    )
    out = bucket_timeline(df, counter_substring="matchme", bucket_seconds=2.0)
    assert not out.empty
    # Filter must have dropped 'Ignore' rows
    assert all("matchme" in c.lower() for c in out["Counter"])


def test_compare_summaries_marks_one_sided():
    base = pd.DataFrame(
        [{"Counter": "A", "Mean": 10.0}, {"Counter": "B", "Mean": 20.0}]
    )
    test = pd.DataFrame(
        [{"Counter": "B", "Mean": 25.0}, {"Counter": "C", "Mean": 5.0}]
    )
    out = compare_summaries(base, test)
    assert not out.empty
    statuses = set(out["Status"].tolist())
    assert "baseline-only" in statuses
    assert "test-only" in statuses
    assert "both" in statuses


# --- load_blg + tool surface tests ------------------------------------------


def test_load_blg_round_trip_uses_cache(mock_relog, fixtures_dir: Path, fake_blg: Path):
    mock_relog(fixtures_dir / "system-overview-relog.csv")

    first = load_blg(path=str(fake_blg))
    assert "log_id" in first

    cache_dir = fake_blg.parent / f".perfmon-cache-{fake_blg.stem}"
    assert cache_dir.is_dir()
    assert (cache_dir / "manifest.json").is_file()

    # Second load should hit the cache - we know this because it succeeds
    # even after we point the mock at a missing fixture.
    mock_relog(fixtures_dir / "does-not-exist.csv")
    second = load_blg(path=str(fake_blg))
    assert "cache" in second.lower()


def test_load_blg_missing_file(tmp_path: Path):
    out = load_blg(path=str(tmp_path / "ghost.blg"))
    assert "not found" in out.lower()


def test_get_counter_summary_after_load(mock_relog, fixtures_dir: Path, fake_blg: Path):
    mock_relog(fixtures_dir / "cpu-detailed-relog.csv")
    first = load_blg(path=str(fake_blg))
    log_id = _extract_log_id(first)

    summary = get_counter_summary(log_id=log_id, top_n=10)
    assert "% Processor Time" in summary or "Processor" in summary


def test_get_counter_timeline_filters(mock_relog, fixtures_dir: Path, fake_blg: Path):
    mock_relog(fixtures_dir / "cpu-detailed-relog.csv")
    log_id = _extract_log_id(load_blg(path=str(fake_blg)))

    timeline = get_counter_timeline(
        log_id=log_id, counter="% Processor Time", bucket_seconds=1.0
    )
    assert "BucketStart" in timeline or "bucket" in timeline.lower() or "%" in timeline


def test_get_per_queue_summary_picks_up_rqnum(mock_relog, fixtures_dir: Path, fake_blg: Path):
    mock_relog(fixtures_dir / "mellanox-rss-relog.csv")
    log_id = _extract_log_id(load_blg(path=str(fake_blg)))

    out = get_per_queue_summary(log_id=log_id)
    lower = out.lower()
    assert "rqnum" in lower or "sqnum" in lower or "queue" in lower


def test_compare_logs_two_loads(mock_relog, fixtures_dir: Path, tmp_path: Path):
    blg_a = tmp_path / "a.blg"
    blg_b = tmp_path / "b.blg"
    blg_a.write_bytes(b"A" * 1024)
    blg_b.write_bytes(b"B" * 2048)

    mock_relog(fixtures_dir / "system-overview-relog.csv")
    a = _extract_log_id(load_blg(path=str(blg_a)))
    b = _extract_log_id(load_blg(path=str(blg_b)))

    out = compare_logs(baseline_log_id=a, test_log_id=b)
    assert "Counter" in out or "Delta" in out


def test_list_loaded_logs_empty_message():
    out = list_loaded_logs()
    assert "no" in out.lower() or "load_blg" in out.lower()


def test_require_log_unknown_id():
    import pytest

    from perfmon_mcp.log_state import require_log

    with pytest.raises(ValueError):
        require_log("log_nope")


def _extract_log_id(markdown: str) -> str:
    """Pull the ``log_id`` value out of a load_blg markdown summary."""
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
