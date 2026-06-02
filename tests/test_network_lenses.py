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
