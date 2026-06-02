"""Loaded .blg registry.

Mirrors etw-mcp's ``trace_state`` but scoped to perfmon counter logs.
A ``log_id`` is stable per .blg version: ``"log_<sha256[:12]>"`` of
``"<lowercase resolved path>|<size>|<mtime_ns>"``.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass
class LogData:
    """Cached data from a loaded .blg perfmon log."""

    log_id: str
    blg_path: Path
    export_dir: Path

    producer: str = "relog"

    dataframes: dict[str, pd.DataFrame] = field(default_factory=dict)

    duration_seconds: float | None = None
    sample_count: int | None = None
    counter_count: int | None = None
    hostnames: list[str] = field(default_factory=list)
    export_errors: list[str] = field(default_factory=list)

    lock: Any = field(default_factory=threading.RLock, repr=False)

    @property
    def counters(self) -> pd.DataFrame | None:
        return self.dataframes.get("counters")

    @property
    def summary(self) -> pd.DataFrame | None:
        return self.dataframes.get("summary")


_logs: dict[str, LogData] = {}
_registry_lock = threading.RLock()


def make_log_id(blg_path: Path) -> str:
    """Create a stable ID for the current version of a .blg file."""
    path = blg_path.resolve()
    stat = path.stat()
    key = f"{str(path).lower()}|{stat.st_size}|{stat.st_mtime_ns}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"log_{digest}"


def register_log(log: LogData) -> str:
    """Register a loaded log and return its log_id."""
    with _registry_lock:
        _logs[log.log_id] = log
    return log.log_id


def get_log(log_id: str) -> LogData | None:
    """Return a loaded log by ID, or None if unknown."""
    with _registry_lock:
        return _logs.get(log_id)


def require_log(log_id: str) -> LogData:
    """Get a loaded log by ID or raise a helpful ValueError."""
    if not log_id:
        raise ValueError(
            "log_id is required. Call load_blg first and pass the returned log_id."
        )

    log = get_log(log_id)
    if log is None:
        loaded = list_loaded_log_ids()
        if loaded:
            loaded_msg = ", ".join(f"`{lid}`" for lid in loaded)
            raise ValueError(
                f"Unknown log_id `{log_id}`. Loaded log IDs: {loaded_msg}"
            )
        raise ValueError(
            f"Unknown log_id `{log_id}`. No logs are loaded. Call load_blg first."
        )
    return log


def list_loaded_logs() -> list[LogData]:
    """Return all loaded logs."""
    with _registry_lock:
        return list(_logs.values())


def list_loaded_log_ids() -> list[str]:
    """Return all loaded log IDs sorted alphabetically."""
    with _registry_lock:
        return sorted(_logs)


def unregister_log(log_id: str) -> bool:
    """Remove a loaded log from the registry."""
    with _registry_lock:
        return _logs.pop(log_id, None) is not None


def clear_logs() -> None:
    """Clear all loaded logs. Intended for tests."""
    with _registry_lock:
        _logs.clear()
