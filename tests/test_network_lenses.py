"""Tests for get_counter_throughput (NIC convenience lens)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from perfmon_mcp.tools.analyze import load_log
from perfmon_mcp.tools.network_lenses import get_counter_throughput


def _make_blg(path: Path) -> Path:
    path.write_bytes(b"PERFBINARY-FAKE\x00" * 64)
    return path


_NETWORK_CSV = """(PDH-CSV 4.0) (Pacific Standard Time)(480),\\\\HOST\\Network Adapter(Mellanox ConnectX-6 Dx Adapter _2)\\Bytes Total/sec,\\\\HOST\\Network Adapter(Mellanox ConnectX-6 Dx Adapter _2)\\Packets/sec,\\\\HOST\\Network Adapter(Mellanox ConnectX-6 Dx Adapter _2)\\Packets Outbound Errors,\\\\HOST\\Processor(_Total)\\% Processor Time
"01/01/2024 12:00:00.000","1000000","1000","0","12.5"
"01/01/2024 12:00:01.000","2000000","2000","0","15.0"
"01/01/2024 12:00:02.000","3000000","3000","1","18.5"
"""


def _extract_log_id(text: str) -> str:
    for line in text.splitlines():
        if "`log_id`" in line and "`log_" in line.split("`log_id`")[1]:
            # "- `log_id`: `log_<sha>`"
            tail = line.split("`log_id`")[1]
            return tail.split("`")[1]
    raise AssertionError(f"could not find log_id in:\n{text}")


def test_get_counter_throughput_returns_table(
    tmp_path: Path, mock_relog, fixtures_dir: Path
) -> None:
    blg = _make_blg(tmp_path / "net.blg")
    csv_fixture = tmp_path / "net.csv"
    csv_fixture.write_text(_NETWORK_CSV)
    mock_relog(csv_fixture)
    out = load_log(path=str(blg))
    log_id = _extract_log_id(out)

    result = get_counter_throughput(log_id=log_id, nic_filter="*")
    assert "NIC throughput" in result
    assert "Bytes Total/sec" in result
    assert "Packets/sec" in result
    # Processor counter must NOT be in the table.
    assert "% Processor Time" not in result


def test_get_counter_throughput_nic_filter_scopes_to_one_nic(
    tmp_path: Path, mock_relog
) -> None:
    blg = _make_blg(tmp_path / "net2.blg")
    csv_fixture = tmp_path / "net2.csv"
    csv_fixture.write_text(_NETWORK_CSV)
    mock_relog(csv_fixture)
    out = load_log(path=str(blg))
    log_id = _extract_log_id(out)

    # Filter that matches the NIC.
    result = get_counter_throughput(log_id=log_id, nic_filter="Mellanox")
    assert "Bytes Total/sec" in result

    # Filter that does NOT match.
    result_miss = get_counter_throughput(log_id=log_id, nic_filter="Intel")
    assert "No Network Adapter throughput counters" in result_miss
    assert "Intel" in result_miss


def test_get_counter_throughput_no_network_counters(
    tmp_path: Path, mock_relog, fixtures_dir: Path
) -> None:
    # cpu-detailed fixture has no Network Adapter rows.
    blg = _make_blg(tmp_path / "cpu.blg")
    mock_relog(fixtures_dir / "cpu-detailed-relog.csv")
    out = load_log(path=str(blg))
    log_id = _extract_log_id(out)

    result = get_counter_throughput(log_id=log_id)
    assert "No Network Adapter throughput counters" in result


def test_get_counter_throughput_unknown_log_id() -> None:
    with pytest.raises(ValueError):
        get_counter_throughput(log_id="log_does_not_exist")


# ---------------------------------------------------------------------------
# v0.3: get_rss_distribution
# ---------------------------------------------------------------------------


# Curated counter-name CSV with the 13 mellanox RSS names. Two adapters
# (Adapter _2 and Adapter _3), four Cpu_N + two RqNum_N instances, all
# monotonic so per_queue_aggregate produces clean Delta values.
_RSS_CSV = (
    "(PDH-CSV 4.0) (Pacific Standard Time)(480),"
    "\\\\HOST\\Mellanox WinOF-2 Rss Counters(Adapter _2 + Cpu_0)\\Rss IPv4/Udp,"
    "\\\\HOST\\Mellanox WinOF-2 Rss Counters(Adapter _2 + Cpu_1)\\Rss IPv4/Udp,"
    "\\\\HOST\\Mellanox WinOF-2 Rss Counters(Adapter _3 + Cpu_0)\\Rss IPv4/Udp,"
    "\\\\HOST\\Mellanox WinOF-2 Receive Datapath Counters(Adapter _2 + RqNum_0)\\Packets processed in NDIS poll mode,"
    "\\\\HOST\\Mellanox WinOF-2 Receive Datapath Counters(Adapter _2 + RqNum_1)\\Packets processed in NDIS poll mode\n"
    '"01/01/2024 12:00:00.000","100","200","50","1000","1000"\n'
    '"01/01/2024 12:00:01.000","200","400","100","2000","2000"\n'
    '"01/01/2024 12:00:02.000","300","600","150","3000","3000"\n'
)


def test_get_rss_distribution_returns_curated_table(
    tmp_path: Path, mock_relog
) -> None:
    from perfmon_mcp.tools.network_lenses import get_rss_distribution

    blg = _make_blg(tmp_path / "rss.blg")
    csv_fixture = tmp_path / "rss.csv"
    csv_fixture.write_text(_RSS_CSV)
    mock_relog(csv_fixture)
    out = load_log(path=str(blg))
    log_id = _extract_log_id(out)

    result = get_rss_distribution(log_id=log_id)
    assert "RSS distribution" in result
    # Three Cpu_N rows from Adapter _2 + _3 are visible
    assert "Cpu" in result
    # Per-RqNum section
    assert "RqNum" in result


def test_get_rss_distribution_adapter_filter_scopes_to_one_nic(
    tmp_path: Path, mock_relog
) -> None:
    from perfmon_mcp.tools.network_lenses import get_rss_distribution

    blg = _make_blg(tmp_path / "rss2.blg")
    csv_fixture = tmp_path / "rss2.csv"
    csv_fixture.write_text(_RSS_CSV)
    mock_relog(csv_fixture)
    out = load_log(path=str(blg))
    log_id = _extract_log_id(out)

    result = get_rss_distribution(log_id=log_id, adapter_filter="Adapter _2")
    assert "Adapter _2" in result
    assert "Adapter _3" not in result


def test_get_rss_distribution_no_matching_counters_message(
    tmp_path: Path, mock_relog, fixtures_dir: Path
) -> None:
    """A log without any of the 13 curated names produces a friendly miss
    instead of an empty table."""
    from perfmon_mcp.tools.network_lenses import get_rss_distribution

    blg = _make_blg(tmp_path / "sys.blg")
    mock_relog(fixtures_dir / "system-overview-relog.csv")
    out = load_log(path=str(blg))
    log_id = _extract_log_id(out)

    result = get_rss_distribution(log_id=log_id)
    # Either no per-queue counters at all, or no curated names matched.
    lower = result.lower()
    assert "no" in lower and ("rss" in lower or "per-queue" in lower or "queue" in lower)


def test_get_rss_distribution_unknown_log_id() -> None:
    from perfmon_mcp.tools.network_lenses import get_rss_distribution

    with pytest.raises(ValueError):
        get_rss_distribution(log_id="log_does_not_exist")

