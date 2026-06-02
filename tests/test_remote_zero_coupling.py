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
