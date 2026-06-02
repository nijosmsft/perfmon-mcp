"""Tests for ``parsing/relog.parse_relog_csv`` BOM handling.

PerfMon GUI exports are UTF-16 LE BOM CSVs. The previous parser had no
``encoding=`` argument and let pandas default to UTF-8, which silently
parsed the file to zero rows (the friendly "parsed to zero rows" error
hides the actual cause). The fix sniffs the first 4 bytes for a BOM
and passes the right encoding to ``pd.read_csv``.
"""

from __future__ import annotations

import codecs
from pathlib import Path

from perfmon_mcp.parsing.relog import _detect_csv_encoding, parse_relog_csv


_SAMPLE_CSV_TEXT = (
    '"(PDH-CSV 4.0) (Pacific Standard Time)(480)",'
    '"\\\\HOST\\Processor(_Total)\\% Processor Time"\n'
    '"12/01/2026 10:00:00.000","12.5"\n'
    '"12/01/2026 10:00:01.000","15.2"\n'
    '"12/01/2026 10:00:02.000","18.7"\n'
)


def _expected_columns() -> set[str]:
    return {
        "Timestamp",
        "FullPath",
        "Hostname",
        "Object",
        "Instance",
        "Counter",
        "Value",
    }


def test_detect_encoding_utf8_no_bom(tmp_path: Path) -> None:
    p = tmp_path / "ascii.csv"
    p.write_bytes(_SAMPLE_CSV_TEXT.encode("utf-8"))
    assert _detect_csv_encoding(p) == "utf-8"


def test_detect_encoding_utf16_le_bom(tmp_path: Path) -> None:
    p = tmp_path / "utf16le.csv"
    p.write_bytes(codecs.BOM_UTF16_LE + _SAMPLE_CSV_TEXT.encode("utf-16-le"))
    assert _detect_csv_encoding(p) == "utf-16-le"


def test_detect_encoding_utf16_be_bom(tmp_path: Path) -> None:
    p = tmp_path / "utf16be.csv"
    p.write_bytes(codecs.BOM_UTF16_BE + _SAMPLE_CSV_TEXT.encode("utf-16-be"))
    assert _detect_csv_encoding(p) == "utf-16-be"


def test_detect_encoding_utf8_bom(tmp_path: Path) -> None:
    p = tmp_path / "utf8sig.csv"
    p.write_bytes(codecs.BOM_UTF8 + _SAMPLE_CSV_TEXT.encode("utf-8"))
    assert _detect_csv_encoding(p) == "utf-8-sig"


def test_parse_relog_csv_handles_utf16_le_bom(tmp_path: Path) -> None:
    # Write a real UTF-16 LE BOM CSV — the exact shape PerfMon GUI
    # exports produce. Without BOM handling, pandas defaults to UTF-8
    # and the parse silently yields zero rows.
    p = tmp_path / "perfmon-gui-export.csv"
    p.write_bytes(codecs.BOM_UTF16_LE + _SAMPLE_CSV_TEXT.encode("utf-16-le"))

    df = parse_relog_csv(p)
    assert not df.empty, "expected UTF-16 LE BOM CSV to parse to non-empty rows"
    assert _expected_columns().issubset(set(df.columns))
    # Three sample rows with a numeric value each.
    assert len(df) == 3
    assert df["Counter"].iloc[0] == "% Processor Time"
    assert df["Hostname"].iloc[0] == "HOST"
    assert float(df["Value"].iloc[0]) == 12.5


def test_parse_relog_csv_handles_utf8_bom(tmp_path: Path) -> None:
    p = tmp_path / "utf8sig-export.csv"
    p.write_bytes(codecs.BOM_UTF8 + _SAMPLE_CSV_TEXT.encode("utf-8"))

    df = parse_relog_csv(p)
    assert not df.empty
    assert _expected_columns().issubset(set(df.columns))
    assert len(df) == 3


def test_parse_relog_csv_ascii_unchanged(tmp_path: Path) -> None:
    # The original relog.exe -f CSV output is ASCII (no BOM). The BOM
    # sniff must not regress that path.
    p = tmp_path / "ascii.csv"
    p.write_bytes(_SAMPLE_CSV_TEXT.encode("utf-8"))

    df = parse_relog_csv(p)
    assert not df.empty
    assert len(df) == 3
    assert _expected_columns().issubset(set(df.columns))
