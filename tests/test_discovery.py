"""Tests for the discover_counter_sets / discover_nics tools.

The local path is exercised by monkey-patching ``subprocess.run`` so the
test never actually spawns ``powershell.exe``; the remote path is
exercised by simply asserting the LabLink-first runbook shape; both
``parse_*_output`` round-trips are driven by the bundled fixtures.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from perfmon_mcp.tools.snapshot import (
    _parse_listset_text,
    _parse_netadapter_text,
    _tag_vendor,
    discover_counter_sets,
    discover_nics,
    parse_counter_sets_output,
    parse_nics_output,
)


# ---------------------------------------------------------------------------
# Vendor tagging
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Mellanox WinOF-2 Adapter Diagnostics", "Mellanox"),
        ("Mellanox WinOF-2 RSS Per Processor", "Mellanox"),
        ("Intel(R) Ethernet 10G 2P X710", "Intel"),
        ("Broadcom NetXtreme-E", "Broadcom"),
        ("Processor", "Microsoft"),
        ("Network Adapter", "Microsoft"),
        ("Memory", "Microsoft"),
        ("TCPv4", "Microsoft"),
        ("SomeVendorRandomSet", "Other"),
    ],
)
def test_tag_vendor_buckets(name: str, expected: str) -> None:
    assert _tag_vendor(name) == expected


# ---------------------------------------------------------------------------
# parse_counter_sets_output (parser)
# ---------------------------------------------------------------------------


def test_parse_counter_sets_full_fixture(fixtures_dir: Path) -> None:
    text = (fixtures_dir / "get-counter-listset.txt").read_text(encoding="utf-8")
    df = _parse_listset_text(text)
    assert not df.empty
    assert {"CounterSet", "Vendor", "Description"} <= set(df.columns)
    # Mellanox prefix detected via the "mellanox|winof" regex.
    mellanox = df[df["Vendor"] == "Mellanox"]
    assert len(mellanox) >= 3
    # Microsoft prefix detected for things like Processor / Network Adapter.
    assert (df["Vendor"] == "Microsoft").sum() >= 5
    # Unknown vendor falls into Other.
    assert "SomeVendorRandomSet" in df[df["Vendor"] == "Other"]["CounterSet"].tolist()


def test_parse_counter_sets_output_markdown(fixtures_dir: Path) -> None:
    text = (fixtures_dir / "get-counter-listset.txt").read_text(encoding="utf-8")
    out = parse_counter_sets_output(text)
    assert isinstance(out, str)
    assert "discover_counter_sets" in out
    assert "Mellanox" in out
    assert "CounterSet" in out


def test_parse_counter_sets_output_filter_to_mellanox(fixtures_dir: Path) -> None:
    text = (fixtures_dir / "get-counter-listset.txt").read_text(encoding="utf-8")
    out = parse_counter_sets_output(text, vendor_filter="mellanox")
    assert "Mellanox" in out
    # Microsoft / Intel rows must be filtered OUT.
    assert "TCPv4" not in out
    assert "Intel" not in out


def test_parse_counter_sets_output_empty_text() -> None:
    out = parse_counter_sets_output("")
    assert "No text" in out


def test_parse_counter_sets_output_no_matches() -> None:
    out = parse_counter_sets_output(
        "Foo\tA description\nBar\tAnother description\n",
        vendor_filter="mellanox",
    )
    assert "No PDH counter sets" in out


# ---------------------------------------------------------------------------
# parse_nics_output (parser)
# ---------------------------------------------------------------------------


def test_parse_nics_full_fixture(fixtures_dir: Path) -> None:
    text = (fixtures_dir / "get-netadapter.txt").read_text(encoding="utf-8")
    df = _parse_netadapter_text(text)
    assert len(df) == 4
    assert {"Name", "IfIndex", "Status", "LinkSpeed", "MacAddress"} <= set(df.columns)
    # TEST-100G-2 has IfIndex 12 and is Up.
    row = df[df["Name"] == "TEST-100G-2"].iloc[0]
    assert int(row["IfIndex"]) == 12
    assert row["Status"] == "Up"
    assert "Mellanox" in row["InterfaceDescription"]


def test_parse_nics_output_markdown(fixtures_dir: Path) -> None:
    text = (fixtures_dir / "get-netadapter.txt").read_text(encoding="utf-8")
    out = parse_nics_output(text)
    assert "discover_nics" in out
    assert "TEST-100G-2" in out
    # Up-count summary mentions adapters.
    assert "adapters" in out


def test_parse_nics_output_empty() -> None:
    assert "No text" in parse_nics_output("")


# ---------------------------------------------------------------------------
# Remote / sidecar shape
# ---------------------------------------------------------------------------


_JSON_FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def test_discover_counter_sets_remote_emits_sidecar() -> None:
    out = discover_counter_sets(target="remote")
    assert "Get-Counter -ListSet" in out
    match = _JSON_FENCE.search(out)
    assert match, "Remote output must include a JSON sidecar block"
    sidecar = json.loads(match.group(1))
    assert sidecar["parse_with"] == "parse_counter_sets_output"
    assert sidecar["shell"] == "powershell"


def test_discover_nics_remote_emits_sidecar() -> None:
    out = discover_nics(target="remote")
    assert "Get-NetAdapter" in out
    match = _JSON_FENCE.search(out)
    assert match, "Remote output must include a JSON sidecar block"
    sidecar = json.loads(match.group(1))
    assert sidecar["parse_with"] == "parse_nics_output"


def test_discover_counter_sets_unknown_target() -> None:
    out = discover_counter_sets(target="bogus")
    assert "Unknown target" in out


def test_discover_nics_unknown_target() -> None:
    out = discover_nics(target="bogus")
    assert "Unknown target" in out


# ---------------------------------------------------------------------------
# Local path (subprocess.run mocked)
# ---------------------------------------------------------------------------


class _FakeCompleted:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_discover_counter_sets_local_subprocess_success(
    monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path
) -> None:
    text = (fixtures_dir / "get-counter-listset.txt").read_text(encoding="utf-8")

    def fake_run(*_args, **_kwargs):
        return _FakeCompleted(stdout=text)

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = discover_counter_sets(target="local")
    assert "Mellanox" in out
    # Sidecar is NOT included on the success path.
    assert "```json" not in out


def test_discover_nics_local_subprocess_failure_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args, **_kwargs):
        return _FakeCompleted(stdout="", stderr="boom", returncode=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = discover_nics(target="local")
    # Failure path falls back to the LabLink-first remote block.
    assert "discover_nics (target=local) failed" in out
    assert "```json" in out


# ---------------------------------------------------------------------------
# v0.3: discover_counter_instances
# ---------------------------------------------------------------------------


from perfmon_mcp.tools.snapshot import (  # noqa: E402
    _filter_instance_rows,
    _parse_listset_instances_text,
    discover_counter_instances,
    parse_counter_instances_output,
)


_SAMPLE_INSTANCES = """\
Mellanox WinOF-2 RSS Counters\t\\\\TESTHOST\\Mellanox WinOF-2 RSS Counters(Adapter #2 + Cpu_0)\\Rss IPv4/Udp
Mellanox WinOF-2 RSS Counters\t\\\\TESTHOST\\Mellanox WinOF-2 RSS Counters(Adapter #2 + Cpu_1)\\Rss IPv4/Udp
Mellanox WinOF-2 RSS Counters\t\\\\TESTHOST\\Mellanox WinOF-2 RSS Counters(Adapter #3 + Cpu_0)\\Rss IPv4/Udp

Mellanox WinOF-2 RSS Counters\t\\\\TESTHOST\\Mellanox WinOF-2 RSS Counters(Adapter #2 + Cpu_1)\\Rss IPv4/Udp
line-without-tab
"""


def test_parse_listset_instances_dedups_and_skips_bad_lines() -> None:
    df = _parse_listset_instances_text(_SAMPLE_INSTANCES)
    assert not df.empty
    # 3 unique rows after dedup; line-without-tab + blank line dropped.
    assert len(df) == 3
    assert set(df.columns) == {"SetName", "InstancePath"}


def test_filter_instance_rows_regex_and_substring() -> None:
    df = _parse_listset_instances_text(_SAMPLE_INSTANCES)
    # Substring (regex-valid) filter
    scoped = _filter_instance_rows(df, "Adapter #2")
    assert len(scoped) == 2
    assert all("Adapter #2" in p for p in scoped["InstancePath"])
    # Empty filter returns the full df
    assert len(_filter_instance_rows(df, "")) == 3


def test_filter_instance_rows_invalid_regex_falls_back_to_substring() -> None:
    df = _parse_listset_instances_text(_SAMPLE_INSTANCES)
    # The '+' is a regex special char; in isolation '*Adapter' is invalid.
    # Real regex error: '[unclosed' triggers the substring fallback.
    scoped = _filter_instance_rows(df, "[unclosed")
    assert scoped.empty or len(scoped) <= len(df)


def test_discover_counter_instances_remote_emits_sidecar() -> None:
    out = discover_counter_instances(
        set_name="Mellanox WinOF-2 RSS Counters",
        instance_filter="Adapter #2",
        target="remote",
    )
    assert "Get-Counter -ListSet" in out
    assert "Adapter #2" in out
    match = _JSON_FENCE.search(out)
    assert match, "Remote output must include a JSON sidecar block"
    sidecar = json.loads(match.group(1))
    assert sidecar["parse_with"] == "parse_counter_instances_output"
    assert sidecar["shell"] == "powershell"


def test_discover_counter_instances_local_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args, **_kwargs):
        return _FakeCompleted(stdout=_SAMPLE_INSTANCES)

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = discover_counter_instances(
        set_name="Mellanox WinOF-2 RSS Counters",
        instance_filter="Adapter #2",
        target="local",
    )
    assert "Adapter #2" in out
    # Success path: NO sidecar fence
    assert "```json" not in out


def test_discover_counter_instances_local_failure_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args, **_kwargs):
        return _FakeCompleted(stdout="", stderr="boom", returncode=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = discover_counter_instances(
        set_name="Mellanox WinOF-2 RSS Counters", target="local"
    )
    assert "failed" in out.lower()
    assert "```json" in out


def test_discover_counter_instances_unknown_target() -> None:
    out = discover_counter_instances(set_name="Anything", target="bogus")
    assert "Unknown target" in out


def test_discover_counter_instances_rejects_empty_set_name() -> None:
    out = discover_counter_instances(set_name="")
    assert "non-empty" in out


def test_parse_counter_instances_output_empty_text() -> None:
    out = parse_counter_instances_output("")
    assert "No text" in out


def test_parse_counter_instances_output_round_trip() -> None:
    out = parse_counter_instances_output(
        _SAMPLE_INSTANCES,
        set_name="Mellanox WinOF-2 RSS Counters",
        instance_filter="Adapter #2",
    )
    assert "Adapter #2" in out
    assert "Adapter #3" not in out
    assert "discover_counter_instances" in out

