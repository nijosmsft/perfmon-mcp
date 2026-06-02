"""Shared pytest fixtures.

The relog.exe shellout is mocked so tests never touch the real
Windows perf subsystem. ``mock_relog`` patches
``perfmon_mcp.parsing.relog._run_relog`` to copy a chosen fixture CSV
into the requested output path, then the regular ``parse_relog_csv``
parses the fixture.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def mock_relog(monkeypatch: pytest.MonkeyPatch) -> Callable[[Path], None]:
    """Return a setter that swaps the relog shellout for a fixture copy.

    Usage::

        def test_thing(mock_relog, fixtures_dir):
            mock_relog(fixtures_dir / "cpu-detailed-relog.csv")
            # ... now load_blg(...) will succeed without a real relog.exe
    """
    state: dict[str, Path] = {}

    def _set(fixture_csv: Path) -> None:
        state["fixture"] = fixture_csv

    def _fake_run_relog(blg_path: Path, csv_path: Path) -> tuple[bool, str]:
        src = state.get("fixture")
        if src is None or not src.is_file():
            return (False, "mock_relog fixture not configured")
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, csv_path)
        return (True, "")

    monkeypatch.setattr(
        "perfmon_mcp.parsing.relog._run_relog",
        _fake_run_relog,
    )
    return _set


@pytest.fixture
def fake_blg(tmp_path: Path) -> Path:
    """Create a non-empty placeholder .blg in tmp so make_log_id works."""
    blg = tmp_path / "test.blg"
    blg.write_bytes(b"PERFBINARY-FAKE\x00" * 64)
    return blg


@pytest.fixture(autouse=True)
def _isolate_log_registry():
    """Each test gets a clean log registry."""
    from perfmon_mcp.log_state import clear_logs

    clear_logs()
    yield
    clear_logs()
