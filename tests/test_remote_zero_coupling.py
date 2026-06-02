"""Critical constraint: src/ must not import or reference any orchestration product.

The whole remote-friendly contract depends on this. ``LabLink`` is named
in user-facing docs (README, get_capture_instructions runbook) as ONE
example MCP transport, but ZERO code in src/ may import lablink_*,
reference any LABLINK_* env var, or take a lablink-specific argument.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"


def _walk_python_sources() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def test_no_lablink_imports():
    bad = []
    pattern = re.compile(r"^\s*(?:import|from)\s+lablink", re.IGNORECASE | re.MULTILINE)
    for path in _walk_python_sources():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            bad.append(str(path))
    assert not bad, f"src/ must not import lablink. Offenders: {bad}"


def test_no_lablink_env_vars():
    bad = []
    pattern = re.compile(r"LABLINK_[A-Z_]+", re.IGNORECASE)
    for path in _walk_python_sources():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            bad.append((str(path), pattern.search(text).group(0)))
    assert not bad, f"src/ must not reference any LABLINK_* env var. Offenders: {bad}"


def test_no_other_orchestrator_imports():
    bad = []
    pattern = re.compile(
        r"^\s*(?:import|from)\s+(ansible|salt|fabric|paramiko)\b",
        re.IGNORECASE | re.MULTILINE,
    )
    for path in _walk_python_sources():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            bad.append(str(path))
    assert not bad, f"src/ must not import any orchestrator. Offenders: {bad}"


def test_capture_runbook_does_mention_lablink_in_text():
    """Negative-of-the-negative: LabLink IS allowed in tool docstrings /
    user-facing runbook output. Confirm it shows up there, otherwise
    we've over-corrected and lost the documentation."""
    from perfmon_mcp.tools.capture import get_capture_instructions

    out = get_capture_instructions(
        scenario="system-overview", target="remote", output_path="C:\\test.blg"
    )
    assert "lablink" in out.lower(), (
        "LabLink must be named in the remote runbook output as one "
        "example transport - just not imported in src/."
    )


def test_capture_runbook_lablink_appears_before_psremoting():
    """LabLink-first smoothing: the transfer-back block must list the
    LabLink (MCP) transport above the PSRemoting / scp fallbacks so the
    runbook reads as 'prefer LabLink, fallback to PSRemoting'."""
    from perfmon_mcp.tools.capture import get_capture_instructions

    out = get_capture_instructions(
        scenario="system-overview", target="remote", output_path="C:\\test.blg"
    )
    lower = out.lower()
    lablink_idx = lower.find("lablink")
    psr_idx = lower.find("powershell remoting")
    assert lablink_idx >= 0 and psr_idx >= 0, (
        "Both LabLink and PowerShell remoting transports must be present "
        "in the remote runbook output."
    )
    assert lablink_idx < psr_idx, (
        "LabLink must appear before PowerShell remoting in the remote "
        "runbook output (LabLink-first smoothing)."
    )


def test_remote_sidecars_contain_no_python_imports():
    """JSON sidecars on remote tool output must contain pure transport
    metadata only - no python source, no lablink_*, no import lines.

    Regression guard: the LabLink-first smoothing must not accidentally
    embed orchestration imports inside the JSON block of any emit-only
    tool. We sample one tool per surface (snapshot, capture, discovery)
    and assert the resulting blob has no python ``import`` or ``from``
    statement.
    """
    import json
    import re as _re

    from perfmon_mcp.tools.capture import get_capture_status
    from perfmon_mcp.tools.snapshot import (
        discover_counter_sets,
        discover_nics,
        snapshot_counters,
    )

    outputs = [
        ("snapshot_counters", snapshot_counters("system-overview", target="remote")),
        ("discover_counter_sets", discover_counter_sets(target="remote")),
        ("discover_nics", discover_nics(target="remote")),
        ("get_capture_status", get_capture_status(target="remote")),
    ]

    import_pat = _re.compile(
        r"^\s*(?:import|from)\s+\w", _re.MULTILINE
    )
    lablink_token_pat = _re.compile(r"lablink_[a-z_]+", _re.IGNORECASE)

    json_fence_pat = _re.compile(
        r"```json\s*\n(.*?)\n```", _re.DOTALL
    )

    for label, blob in outputs:
        for match in json_fence_pat.finditer(blob):
            json_text = match.group(1)
            # Must parse cleanly.
            parsed = json.loads(json_text)
            # Sidecar must be flat primitive key/values.
            for key, value in parsed.items():
                assert isinstance(key, str), (
                    f"{label}: JSON sidecar key {key!r} is not a string"
                )
                assert isinstance(value, (str, int, float, bool, type(None))), (
                    f"{label}: JSON sidecar value for {key!r} is not a primitive: "
                    f"{type(value).__name__}"
                )
            # Sidecar must NOT contain python import statements.
            assert not import_pat.search(json_text), (
                f"{label}: JSON sidecar contains a python import statement"
            )
            # Sidecar must NOT contain LABLINK_* env var references.
            assert not lablink_token_pat.search(json_text), (
                f"{label}: JSON sidecar contains a lablink_* token (sidecars "
                "are transport-agnostic by contract)"
            )
