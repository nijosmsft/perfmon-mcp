"""Evidence federation: graceful no-op when env var / library is missing."""

from __future__ import annotations

import os
from pathlib import Path

from perfmon_mcp.evidence_integration import (
    ENV_VAR,
    is_configured,
    safe_register_entities_from_log,
)
from perfmon_mcp.tools.analyze import load_blg
from perfmon_mcp.tools.evidence import get_entities, get_evidence_status


def test_env_var_name_is_perfmon_specific():
    assert ENV_VAR == "PERFMON_MCP_EVIDENCE_PATH"


def test_get_evidence_status_when_unset(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    out = get_evidence_status()
    assert ENV_VAR in out
    assert "library installed" in out.lower()


def test_safe_register_no_op_when_unset(monkeypatch, mock_relog, fixtures_dir: Path, fake_blg: Path):
    monkeypatch.delenv(ENV_VAR, raising=False)
    mock_relog(fixtures_dir / "system-overview-relog.csv")
    load_blg(path=str(fake_blg))
    # safe_register is called inside load_blg; should not raise and should
    # not have registered anything because the env var is unset.
    assert not is_configured()


def test_get_entities_when_unset_returns_message(monkeypatch, mock_relog, fixtures_dir: Path, fake_blg: Path):
    monkeypatch.delenv(ENV_VAR, raising=False)
    mock_relog(fixtures_dir / "system-overview-relog.csv")
    out = load_blg(path=str(fake_blg))
    log_id = _extract_log_id(out)

    result = get_entities(log_id=log_id)
    lower = result.lower()
    # Either library missing OR env var unset - both must produce a
    # friendly message, never an exception.
    assert "evidence" in lower
    assert "not installed" in lower or "unset" in lower


def _extract_log_id(markdown: str) -> str:
    for line in markdown.splitlines():
        if "`log_id`" in line:
            for token in line.split("`"):
                if token.startswith("log_") and token != "log_id":
                    return token
    raise AssertionError(markdown)
