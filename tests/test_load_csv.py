"""Direct tests for ``load_csv`` and the ``load_log`` -> ``load_csv`` route.

The pre-existing test suite exercises ``load_csv`` only indirectly via
``load_log(*.csv)`` in fixtures. These tests pin the contract:

- ``load_csv`` succeeds against a fixture CSV and registers the log.
- ``load_log`` with a ``.csv`` suffix produces an identical log_id to
  a direct ``load_csv`` call (both go through the same cache key).
- ``load_csv`` on a missing path returns a friendly markdown error and
  does not raise.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from perfmon_mcp.log_state import get_log
from perfmon_mcp.tools.analyze import load_csv, load_log


def _extract_log_id(markdown: str) -> str:
    for line in markdown.splitlines():
        if "`log_id`" in line and "log_" in line:
            for token in line.split("`"):
                if (
                    token.startswith("log_")
                    and len(token) > 4
                    and token != "log_id"
                ):
                    return token
    raise AssertionError(f"No log_id in markdown:\n{markdown}")


def test_load_csv_succeeds_and_registers(
    tmp_path: Path, fixtures_dir: Path
) -> None:
    csv = tmp_path / "system-overview.csv"
    shutil.copy2(fixtures_dir / "system-overview-relog.csv", csv)

    out = load_csv(path=str(csv))
    assert "log_id" in out
    log_id = _extract_log_id(out)
    assert log_id.startswith("log_")

    # Registered: get_log returns the LogData.
    log = get_log(log_id)
    assert log is not None
    assert log.blg_path == csv.resolve()
    assert log.counter_count and log.counter_count > 0


def test_load_log_with_csv_path_routes_to_load_csv(
    tmp_path: Path, fixtures_dir: Path
) -> None:
    csv = tmp_path / "system-overview.csv"
    shutil.copy2(fixtures_dir / "system-overview-relog.csv", csv)

    via_load_csv = _extract_log_id(load_csv(path=str(csv)))
    via_load_log = _extract_log_id(load_log(path=str(csv)))

    # Same path -> same log_id (sha256 of path|size|mtime_ns).
    assert via_load_csv == via_load_log


def test_load_csv_missing_file_returns_friendly_error(tmp_path: Path) -> None:
    ghost = tmp_path / "does-not-exist.csv"
    out = load_csv(path=str(ghost))
    assert "not found" in out.lower()
    assert str(ghost) in out or ghost.name in out


def test_load_log_csv_uses_csv_loader_not_relog(
    tmp_path: Path, fixtures_dir: Path, monkeypatch
) -> None:
    # If load_log routed a .csv to the relog path, _run_relog would be
    # called and our monkeypatch would raise. Use this to lock down the
    # routing contract.
    csv = tmp_path / "system-overview.csv"
    shutil.copy2(fixtures_dir / "system-overview-relog.csv", csv)

    def _boom(*_args, **_kwargs):
        raise AssertionError("relog must not be called for .csv inputs")

    monkeypatch.setattr("perfmon_mcp.parsing.relog._run_relog", _boom)

    out = load_log(path=str(csv))
    assert "log_id" in out
