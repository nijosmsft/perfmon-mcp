"""Teardown-emit tests. No live logman/Stop-Process - emit-only contract."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from perfmon_mcp.tools.capture import (
    get_teardown_commands,
    parse_teardown_output,
)

_JSON_FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
_POWERSHELL_FENCE = re.compile(r"```powershell\s*\n(.*?)\n```", re.DOTALL)
# Matches any 'logman stop' or 'logman delete' line/segment that still
# carries a PowerShell common parameter like -ErrorAction. Used as a
# negative regression guard - logman.exe is native, not a cmdlet.
_LOGMAN_NATIVE_BAD_FLAG = re.compile(
    r"logman\s+(stop|delete)[^\n;]*-ErrorAction",
    re.IGNORECASE,
)


def _extract_command(out: str) -> str:
    """Return the first ```powershell fenced block from a tool output.

    Falls back to the whole text when no fence is present (e.g.
    target='local' renders the command inline without a fence).
    """
    match = _POWERSHELL_FENCE.search(out)
    if match:
        return match.group(1)
    return out


def test_get_teardown_commands_default_collector_name():
    out = get_teardown_commands()
    assert "PerfmonMcpWatch" in out
    assert "logman stop" in out
    assert "logman delete" in out
    assert "Stop-Process" in out
    assert "perfmon, typeperf, relog, logman" in out


def test_get_teardown_commands_remote_target_has_sidecar():
    out = get_teardown_commands(collector_name="CustomCollector", target="remote")
    assert "CustomCollector" in out
    match = _JSON_FENCE.search(out)
    assert match, "Remote output must include a JSON sidecar block"
    sidecar = json.loads(match.group(1))
    assert sidecar["parse_with"] == "parse_teardown_output"
    assert sidecar["shell"] == "powershell"
    assert isinstance(sidecar.get("timeout_s"), (int, float))


def test_get_teardown_commands_rejects_empty_collector():
    out = get_teardown_commands(collector_name="")
    assert "non-empty" in out


def test_get_teardown_commands_unknown_target():
    out = get_teardown_commands(target="bogus")
    assert "Unknown target" in out


def test_parse_teardown_output_empty_means_clean():
    out = parse_teardown_output("")
    assert "PerfmonMcpWatch" in out
    assert "Teardown complete" in out or "no output" in out


def test_parse_teardown_output_still_present():
    out = parse_teardown_output(
        "Data Collector Set                Status\nPerfmonMcpWatch                   Running",
        collector_name="PerfmonMcpWatch",
    )
    assert "still mentions" in out or "rerun" in out.lower()
    assert "PerfmonMcpWatch" in out


def test_get_teardown_commands_escapes_single_quotes():
    out = get_teardown_commands(collector_name="It's-A-Test")
    assert "It''s-A-Test" in out


# ---------------------------------------------------------------------------
# v0.3 regression: logman.exe is NATIVE, not a cmdlet.
#
# Before the fix, get_teardown_commands rendered:
#     logman stop 'X' -ErrorAction SilentlyContinue 2>&1 | Out-Null
# logman.exe parses '-ErrorAction' / 'SilentlyContinue' as positional
# arguments, prints "Argument 'EA' is unknown" / "Argument
# 'SilentlyContinue' is unknown", and exits with E_INVALIDARG
# (-2147024809). The trailing '2>&1 | Out-Null' hides the error
# message so the script looks clean while neither 'logman stop' nor
# 'logman delete' actually ran.
#
# These tests lock the fix in place:
#   1. Static-content guard: no 'logman (stop|delete) ... -ErrorAction'
#      anywhere in the emitted command.
#   2. Format-not-broken guard: the canonical verbs / process names /
#      query step are still present.
#   3. (Optional, skipped when powershell.exe is absent) Live execution
#      guard: piping the rendered command into powershell.exe must not
#      surface 'is unknown' or 'parameter is incorrect' on stderr.
# ---------------------------------------------------------------------------


def test_teardown_command_does_not_pass_erroraction_to_native_logman():
    """Regression: logman.exe doesn't accept -ErrorAction (it's a
    native EXE, not a cmdlet). Before the fix the emitted command
    threaded -ErrorAction into both logman stop and logman delete,
    causing both to fail silently."""
    out = get_teardown_commands()
    command = _extract_command(out)
    hits = _LOGMAN_NATIVE_BAD_FLAG.findall(command)
    assert hits == [], (
        "logman.exe is native, not a cmdlet - -ErrorAction passed to "
        f"logman stop/delete crashes the binary. Found {len(hits)} "
        f"violation(s): {hits!r}"
    )


def test_teardown_command_shape_unbroken_by_fix():
    """Belt-and-braces: after dropping -ErrorAction the canonical verbs
    must still be present so the fix doesn't accidentally gut the
    teardown."""
    out = get_teardown_commands()
    command = _extract_command(out)
    for needle in (
        "logman stop",
        "logman delete",
        "Get-Process",
        "Stop-Process",
        "logman query",
        "PerfmonMcpWatch",
    ):
        assert needle in command, f"Teardown lost expected verb/identifier: {needle!r}"


def test_teardown_command_keeps_erroraction_on_stop_process_pipeline():
    """Positive guard for the inverse: Get-Process / Stop-Process ARE
    cmdlets and -ErrorAction IS meaningful there (suppresses
    'no such process' noise). The fix must NOT strip it from those."""
    out = get_teardown_commands()
    command = _extract_command(out)
    assert "Get-Process -Name perfmon, typeperf, relog, logman -ErrorAction SilentlyContinue" in command
    assert "Stop-Process -Force -ErrorAction SilentlyContinue" in command


@pytest.mark.skipif(
    shutil.which("powershell.exe") is None,
    reason="powershell.exe not available in the test environment",
)
def test_teardown_command_runs_cleanly_in_powershell():
    """Live execution check: pipe the rendered command into
    powershell.exe and assert stderr does not contain the 'argument
    unknown' or 'parameter is incorrect' messages logman emits when
    it sees an -ErrorAction it doesn't understand.

    We intentionally use a NON-EXISTENT collector name so that
    logman's only failure mode would be the bug we're guarding
    against - if the command is well-formed, logman simply prints
    'Data Collector Set was not found' to stdout (absorbed by
    Out-Null) and exits without complaining about argument parsing.
    """
    out = get_teardown_commands(collector_name="PerfmonMcpRegressionDoesNotExist")
    command = _extract_command(out)
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    lower = combined.lower()
    # The bug's smoking gun is 'argument ... is unknown' from logman's
    # native arg parser.
    assert "is unknown" not in lower, (
        "logman rejected an argument - the -ErrorAction regression is back. "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "parameter is incorrect" not in lower, (
        "logman returned E_INVALIDARG - regression: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
