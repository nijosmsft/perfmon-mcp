"""Live PDH snapshot tools: ``snapshot_counters`` + ``parse_counter_output``.

Both honor the remote-friendly contract:

- ``snapshot_counters(scenario, target='local')`` runs Get-Counter and
  returns the same markdown table that ``parse_counter_output`` would
  produce from the stdout.
- ``snapshot_counters(scenario, target='remote')`` returns the
  Get-Counter command in a fenced ```powershell``` block; the caller
  dispatches it through any transport and feeds the raw stdout back to
  ``parse_counter_output``.

This MCP itself imports no orchestration library; the remote runbook
gives the human / LLM the command and lets any transport (PowerShell
remoting, LabLink, SSH, RDP paste) dispatch it.
"""

from __future__ import annotations

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

_VALID_TARGETS = ("local", "remote")
_GETCOUNTER_TIMEOUT_S = 60


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


def _run_get_counter_local(command: str) -> tuple[str, str]:
    """Spawn powershell.exe and run the Get-Counter command. Returns (stdout, error)."""
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
            timeout=_GETCOUNTER_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ("", f"Get-Counter timed out after {_GETCOUNTER_TIMEOUT_S}s")
    except OSError as exc:
        return ("", f"Failed to spawn powershell: {exc}")

    if result.returncode != 0:
        err = (result.stderr or "").strip() or f"exit {result.returncode}"
        return (result.stdout or "", err)
    return (result.stdout or "", "")


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


@mcp.tool()
def snapshot_counters(scenario: str, target: str = "local") -> str:
    """Take a single live PDH sample of all counters in ``scenario``.

    Args:
        scenario: One of the IDs from ``list_counter_profiles``.
        target: ``"local"`` runs Get-Counter as a subprocess and
            returns the parsed markdown table. ``"remote"`` returns
            the Get-Counter command in a fenced ```powershell``` block
            for the caller to dispatch through any transport
            (PowerShell remoting, LabLink, SSH, manual paste);
            feed the resulting stdout back to ``parse_counter_output``.
            Default ``"local"``.
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
        return (
            f"**snapshot_counters: scenario `{scenario}` (target=remote)**\n\n"
            "Run on the target via any transport (PowerShell remoting, "
            "LabLink, SSH, manual paste). Capture stdout and feed it "
            f"back to `parse_counter_output(text=<stdout>, scenario='{scenario}')` "
            "to render the same markdown table the local path would "
            "have produced.\n\n"
            "```powershell\n"
            f"{command}\n"
            "```\n"
        )

    stdout, err = _run_get_counter_local(command)
    if err:
        return (
            f"**snapshot_counters: scenario `{scenario}` (target=local) failed**\n\n"
            f"Error: {err}\n\n"
            "Workaround: rerun with `target='remote'` to get the "
            "command and dispatch through any transport.\n\n"
            "```powershell\n"
            f"{command}\n"
            "```\n"
        )
    df = parse_get_counter_text(stdout)
    return _format_samples(scenario, df, stdout)


@mcp.tool()
def parse_counter_output(text: str, scenario: str = "") -> str:
    """Parse raw stdout from a remote ``Get-Counter`` invocation.

    The sibling parser for ``snapshot_counters(target='remote')``:
    when you have the ``Get-Counter`` text output from any remote
    transport (PowerShell remoting, LabLink, SSH, RDP paste), pass it
    here to get the same markdown table the local path would have
    produced.

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
