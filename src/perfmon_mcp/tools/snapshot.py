"""Live PDH snapshot tools + discovery (counter sets, NICs).

Tools:

- ``snapshot_counters(scenario, target='local'|'remote')`` runs
  Get-Counter and returns markdown, or emits the LabLink-first remote
  block with a JSON sidecar.
- ``parse_counter_output(text, scenario)`` rebuilds the local markdown
  table from raw Get-Counter stdout.
- ``discover_counter_sets(target, vendor_filter)`` lists installed PDH
  counter sets (``Get-Counter -ListSet``) and tags each by vendor
  (Mellanox / Intel / Broadcom / Microsoft / Other) so callers can
  pick which sets to capture without trial-and-error.
- ``parse_counter_sets_output(text, vendor_filter)`` rebuilds the
  markdown table from remote stdout.
- ``discover_nics(target)`` enumerates NICs (``Get-NetAdapter``).
- ``parse_nics_output(text)`` rebuilds the markdown table from remote
  stdout.

The MCP itself imports no orchestration library; every ``target='remote'``
path returns ``_remote.format_remote_block`` output so any transport
(LabLink first, PSRemoting fallback, SSH, RDP paste) can dispatch the
command and feed the stdout back to the matching ``parse_*_output`` tool.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pandas as pd

from perfmon_mcp.app import mcp
from perfmon_mcp.formatting.markdown import format_table
from perfmon_mcp.parsing.getcounter import parse_get_counter_text
from perfmon_mcp.profiles.metadata import (
    PROFILES,
    extract_counter_paths,
    load_cset_text,
)
from perfmon_mcp.tools._remote import format_remote_block

_VALID_TARGETS = ("local", "remote")
_GETCOUNTER_TIMEOUT_S = 60
_LISTSET_TIMEOUT_S = 120
_NETADAPTER_TIMEOUT_S = 30


# ---------------------------------------------------------------------------
# Vendor tagging
# ---------------------------------------------------------------------------


_VENDOR_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("Mellanox", re.compile(r"mellanox|winof", re.IGNORECASE)),
    ("Intel", re.compile(r"\bintel\b", re.IGNORECASE)),
    ("Broadcom", re.compile(r"\bbroadcom\b", re.IGNORECASE)),
    (
        "Microsoft",
        re.compile(
            r"^(?:processor|memory|network|tcpv|udpv|ipv|http|"
            r"physicaldisk|logicaldisk|system|cache|hyper-v|wfp|"
            r"netio|nic|teredo|smb|server|microsoft|\.net|"
            r"redirector|process|thread)",
            re.IGNORECASE,
        ),
    ),
)


def _tag_vendor(set_name: str) -> str:
    """Bucket a counter-set name into a coarse vendor label."""
    for vendor, pattern in _VENDOR_PATTERNS:
        if pattern.search(set_name):
            return vendor
    return "Other"


# ---------------------------------------------------------------------------
# Local subprocess helpers
# ---------------------------------------------------------------------------


def _build_get_counter_command(scenario: str) -> str:
    """Render the Get-Counter command for a scenario as a one-liner."""
    counters = extract_counter_paths(load_cset_text(scenario))
    counter_arr = ", ".join(f"'{c}'" for c in counters)
    return (
        f"Get-Counter -Counter @({counter_arr}) "
        "-SampleInterval 1 -MaxSamples 1 | "
        "ForEach-Object { $_.CounterSamples } | "
        "ForEach-Object { \"$($_.Path) : $($_.CookedValue)\" }"
    )


def _build_listset_command() -> str:
    """Render the Get-Counter -ListSet enumeration command.

    Emits ``<set name>\\t<description>`` per line. We deliberately keep
    it tab-separated so the parser does not depend on fragile column
    widths.
    """
    return (
        "Get-Counter -ListSet * | "
        "Sort-Object CounterSetName | "
        "ForEach-Object { \"$($_.CounterSetName)`t$($_.Description)\" }"
    )


def _build_netadapter_command() -> str:
    """Render the Get-NetAdapter command for NIC discovery.

    Tab-separated ``Name<TAB>IfIndex<TAB>Status<TAB>LinkSpeed<TAB>Mac<TAB>Description``.
    """
    return (
        "Get-NetAdapter | "
        "Sort-Object IfIndex | "
        "ForEach-Object { "
        "\"$($_.Name)`t$($_.IfIndex)`t$($_.Status)`t$($_.LinkSpeed)`t"
        "$($_.MacAddress)`t$($_.InterfaceDescription)\" "
        "}"
    )


def _run_powershell_local(command: str, timeout_s: int) -> tuple[str, str]:
    """Spawn powershell.exe and run an arbitrary one-liner."""
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        return ("", "powershell.exe not found on PATH")
    try:
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ("", f"powershell command timed out after {timeout_s}s")
    except OSError as exc:
        return ("", f"Failed to spawn powershell: {exc}")
    if result.returncode != 0:
        err = (result.stderr or "").strip() or f"exit {result.returncode}"
        return (result.stdout or "", err)
    return (result.stdout or "", "")


def _run_get_counter_local(command: str) -> tuple[str, str]:
    return _run_powershell_local(command, _GETCOUNTER_TIMEOUT_S)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _format_samples(scenario: str, df: pd.DataFrame, raw_text: str) -> str:
    """Build the markdown rendering of a parsed Get-Counter output."""
    if df.empty:
        return (
            f"**snapshot_counters: scenario `{scenario}`**\n\n"
            "*No counter samples parsed from Get-Counter output.*\n\n"
            "Raw stdout (first 4 KB):\n\n```text\n"
            f"{(raw_text or '')[:4096]}\n```\n"
        )
    cols = ["Hostname", "Object", "Instance", "Counter", "Value"]
    table = format_table(df[cols], max_rows=200)
    distinct_paths = df["FullPath"].nunique()
    hosts = ", ".join(sorted({h for h in df["Hostname"].tolist() if h}))
    return (
        f"**snapshot_counters: scenario `{scenario}`**\n\n"
        f"Hosts: {hosts or '-'} | distinct paths: {distinct_paths} | "
        f"rows: {len(df)}\n\n"
        f"{table}\n"
    )


def _parse_listset_text(text: str) -> pd.DataFrame:
    """Parse tab-separated ``<set name>\\t<description>`` lines.

    Tolerates blank lines and trailing whitespace. Lines without a tab
    are treated as ``name`` with an empty description.
    """
    rows: list[dict[str, str]] = []
    if not text:
        return pd.DataFrame(columns=["CounterSet", "Vendor", "Description"])
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            continue
        if "\t" in line:
            name, _, desc = line.partition("\t")
        else:
            name, desc = line, ""
        name = name.strip()
        if not name:
            continue
        rows.append(
            {
                "CounterSet": name,
                "Vendor": _tag_vendor(name),
                "Description": desc.strip(),
            }
        )
    df = pd.DataFrame(rows, columns=["CounterSet", "Vendor", "Description"])
    if not df.empty:
        df = df.drop_duplicates(subset=["CounterSet"]).reset_index(drop=True)
    return df


def _filter_counter_sets(df: pd.DataFrame, vendor_filter: str) -> pd.DataFrame:
    if df.empty or not vendor_filter:
        return df
    needle = vendor_filter.strip().lower()
    if not needle:
        return df
    mask = (df["Vendor"].str.lower() == needle) | df["CounterSet"].str.lower().str.contains(
        needle, na=False
    )
    return df[mask].reset_index(drop=True)


def _format_counter_sets(df: pd.DataFrame, vendor_filter: str) -> str:
    if df.empty:
        scope = f" matching `{vendor_filter}`" if vendor_filter else ""
        return f"*No PDH counter sets{scope} were found.*"
    vendor_counts = df["Vendor"].value_counts().to_dict()
    summary = ", ".join(f"{v}={n}" for v, n in sorted(vendor_counts.items()))
    return (
        f"**discover_counter_sets** ({len(df)} sets; {summary})\n\n"
        + format_table(df, max_rows=max(len(df) + 1, 200))
        + "\n\nUse the set name with `Get-Counter -ListSet '<set>'` or "
        "feed any subset into a future custom-profile registration.\n"
    )


def _parse_netadapter_text(text: str) -> pd.DataFrame:
    """Parse tab-separated NIC lines."""
    cols = [
        "Name",
        "IfIndex",
        "Status",
        "LinkSpeed",
        "MacAddress",
        "InterfaceDescription",
    ]
    if not text:
        return pd.DataFrame(columns=cols)
    rows: list[dict[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        while len(parts) < len(cols):
            parts.append("")
        rows.append({c: parts[i].strip() for i, c in enumerate(cols)})
    df = pd.DataFrame(rows, columns=cols)
    if "IfIndex" in df.columns and not df.empty:
        df["IfIndex"] = pd.to_numeric(df["IfIndex"], errors="coerce").astype("Int64")
    return df


def _format_nics(df: pd.DataFrame) -> str:
    if df.empty:
        return "*No network adapters reported by Get-NetAdapter.*"
    up = (df["Status"].str.lower() == "up").sum() if "Status" in df.columns else 0
    return (
        f"**discover_nics** ({len(df)} adapters; {up} Up)\n\n"
        + format_table(df, max_rows=max(len(df) + 1, 50))
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def snapshot_counters(scenario: str, target: str = "local") -> str:
    """Take a single live PDH sample of all counters in ``scenario``.

    Args:
        scenario: One of the IDs from ``list_counter_profiles``.
        target: ``"local"`` runs Get-Counter as a subprocess and
            returns the parsed markdown table. ``"remote"`` returns
            the LabLink-first runbook with the Get-Counter command and
            a JSON sidecar - dispatch via any transport
            (LabLink, PSRemoting, SSH, manual paste) and feed the
            stdout back to ``parse_counter_output``. Default ``"local"``.
    """
    if scenario not in PROFILES:
        valid = ", ".join(sorted(PROFILES))
        return f"Unknown scenario `{scenario}`. Valid: {valid}."
    if target not in _VALID_TARGETS:
        return (
            f"Unknown target `{target}`. Valid: "
            f"{', '.join(_VALID_TARGETS)}."
        )

    command = _build_get_counter_command(scenario)

    if target == "remote":
        intro = (
            f"**snapshot_counters: scenario `{scenario}` (target=remote)**\n\n"
            "Run on the target node, then feed the resulting stdout back to "
            f"`parse_counter_output(text=<stdout>, scenario='{scenario}')` "
            "to render the same markdown table the local path would have "
            "produced."
        )
        return format_remote_block(
            command,
            parse_with="parse_counter_output",
            expected_runtime_s=5,
            timeout_s=_GETCOUNTER_TIMEOUT_S,
            intro=intro,
        )

    stdout, err = _run_get_counter_local(command)
    if err:
        return format_remote_block(
            command,
            parse_with="parse_counter_output",
            expected_runtime_s=5,
            timeout_s=_GETCOUNTER_TIMEOUT_S,
            intro=(
                f"**snapshot_counters: scenario `{scenario}` (target=local) "
                f"failed**\n\nError: {err}\n\n"
                "Workaround: dispatch the command below via any transport "
                "and feed the stdout to `parse_counter_output`."
            ),
        )
    df = parse_get_counter_text(stdout)
    return _format_samples(scenario, df, stdout)


@mcp.tool()
def parse_counter_output(text: str, scenario: str = "") -> str:
    """Parse raw stdout from a remote ``Get-Counter`` invocation.

    Args:
        text: Raw stdout from a ``Get-Counter`` invocation. Both the
            default text rendering and the path-prefixed
            ``Path : CookedValue`` shape emitted by the
            ``snapshot_counters(target='remote')`` command are
            recognized.
        scenario: Optional scenario name, used only in the markdown
            header for orientation. Pass it when known.
    """
    if not text or not text.strip():
        return "*No text provided to parse.*"
    df = parse_get_counter_text(text)
    label = scenario or "(unknown)"
    return _format_samples(label, df, text)


@mcp.tool()
def discover_counter_sets(target: str = "local", vendor_filter: str = "") -> str:
    """List installed PDH counter sets, vendor-tagged.

    Wraps ``Get-Counter -ListSet *``. Each set is tagged into a coarse
    bucket: ``Mellanox`` / ``Intel`` / ``Broadcom`` / ``Microsoft`` /
    ``Other`` so callers can quickly pick out vendor-specific NIC
    counter sets without scrolling through hundreds of names.

    Args:
        target: ``"local"`` spawns powershell.exe. ``"remote"`` returns
            the LabLink-first runbook + JSON sidecar; feed stdout to
            ``parse_counter_sets_output``.
        vendor_filter: Optional case-insensitive substring filter
            applied to BOTH the Vendor tag and the CounterSet name
            (e.g. ``"mellanox"``, ``"intel"``, ``"microsoft"``).
            Default ``""`` shows every set.
    """
    if target not in _VALID_TARGETS:
        return (
            f"Unknown target `{target}`. Valid: "
            f"{', '.join(_VALID_TARGETS)}."
        )

    command = _build_listset_command()

    if target == "remote":
        intro = (
            "**discover_counter_sets (target=remote)**\n\n"
            "Run on the target node, then feed stdout back to "
            f"`parse_counter_sets_output(text=<stdout>, "
            f"vendor_filter='{vendor_filter}')`."
        )
        return format_remote_block(
            command,
            parse_with="parse_counter_sets_output",
            expected_runtime_s=30,
            timeout_s=_LISTSET_TIMEOUT_S,
            intro=intro,
        )

    stdout, err = _run_powershell_local(command, _LISTSET_TIMEOUT_S)
    if err:
        return format_remote_block(
            command,
            parse_with="parse_counter_sets_output",
            expected_runtime_s=30,
            timeout_s=_LISTSET_TIMEOUT_S,
            intro=(
                f"**discover_counter_sets (target=local) failed**\n\n"
                f"Error: {err}\n\n"
                "Dispatch the command below via any transport and feed the "
                "stdout to `parse_counter_sets_output`."
            ),
        )
    df = _filter_counter_sets(_parse_listset_text(stdout), vendor_filter)
    return _format_counter_sets(df, vendor_filter)


@mcp.tool()
def parse_counter_sets_output(text: str, vendor_filter: str = "") -> str:
    """Parse raw stdout from ``Get-Counter -ListSet`` and render the table.

    Args:
        text: Tab-separated ``<set name>\\t<description>`` lines as
            produced by the command emitted by ``discover_counter_sets``.
        vendor_filter: Same filter as ``discover_counter_sets``.
    """
    if not text or not text.strip():
        return "*No text provided to parse.*"
    df = _filter_counter_sets(_parse_listset_text(text), vendor_filter)
    return _format_counter_sets(df, vendor_filter)


@mcp.tool()
def discover_nics(target: str = "local") -> str:
    """Enumerate network adapters via ``Get-NetAdapter``.

    Captures Name, IfIndex, Status, LinkSpeed, MacAddress, and
    InterfaceDescription per adapter. The IfIndex is the same value
    XDP / SWRSS use for ``-Interface`` arguments and is the most useful
    cross-reference when wiring up perf experiments.

    Args:
        target: ``"local"`` spawns powershell.exe. ``"remote"`` returns
            the LabLink-first runbook + JSON sidecar; feed stdout to
            ``parse_nics_output``.
    """
    if target not in _VALID_TARGETS:
        return (
            f"Unknown target `{target}`. Valid: "
            f"{', '.join(_VALID_TARGETS)}."
        )

    command = _build_netadapter_command()

    if target == "remote":
        intro = (
            "**discover_nics (target=remote)**\n\n"
            "Run on the target node, then feed stdout back to "
            "`parse_nics_output(text=<stdout>)`."
        )
        return format_remote_block(
            command,
            parse_with="parse_nics_output",
            expected_runtime_s=5,
            timeout_s=_NETADAPTER_TIMEOUT_S,
            intro=intro,
        )

    stdout, err = _run_powershell_local(command, _NETADAPTER_TIMEOUT_S)
    if err:
        return format_remote_block(
            command,
            parse_with="parse_nics_output",
            expected_runtime_s=5,
            timeout_s=_NETADAPTER_TIMEOUT_S,
            intro=(
                f"**discover_nics (target=local) failed**\n\nError: {err}\n\n"
                "Dispatch the command below via any transport and feed the "
                "stdout to `parse_nics_output`."
            ),
        )
    df = _parse_netadapter_text(stdout)
    return _format_nics(df)


@mcp.tool()
def parse_nics_output(text: str) -> str:
    """Parse raw stdout from the ``discover_nics`` Get-NetAdapter command.

    Args:
        text: Tab-separated ``Name\\tIfIndex\\tStatus\\tLinkSpeed\\tMac\\tDescription``
            lines as produced by the command emitted by ``discover_nics``.
    """
    if not text or not text.strip():
        return "*No text provided to parse.*"
    df = _parse_netadapter_text(text)
    return _format_nics(df)
