"""relog.exe wrapper + CSV parser + cache manifest read/write.

The shellout is a single ``relog.exe <blg> -o <csv> -f csv -t 1``.
``-t 1`` means "every sample" (no downsampling); larger values are a
post-MVP tuning knob. Output is read with pandas and reshaped into a
long-form DataFrame: one row per (timestamp, counter path, value).

Cache contract (manifest schema v1):

    {
      "schema_version": 1,
      "producer": "relog",        # or "native" / "csharp-pdh" later
      "created_at": "...",
      "blg_path": "...",
      "blg_mtime_ns": 1234567890,
      "blg_size": 9876543,
      "dataframes": {
        "counters": "counters.parquet",
        "summary": "summary.parquet"
      }
    }

The ``producer`` field is reserved so a future native-PDH or C#
sidecar can drop in without a schema migration. All three implementations
must produce the same parquet schema.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from perfmon_mcp.parsing.aggregator import (
    split_counter_path,
    summarize_counters,
)

CACHE_SCHEMA_VERSION = 1
CACHE_PRODUCER_DEFAULT = "relog"
CACHE_VALID_PRODUCERS = {"relog", "native", "csharp-pdh"}

_RELOG_TIMEOUT_S = 600


@dataclass(frozen=True)
class CacheManifest:
    """Parsed manifest.json content from a perfmon cache directory."""

    schema_version: int
    producer: str
    created_at: str
    blg_path: str
    blg_mtime_ns: int
    blg_size: int
    dataframes: dict[str, str]


def cache_dir_for(blg_path: Path) -> Path:
    """Return ``.perfmon-cache-<stem>/`` next to the .blg file."""
    return blg_path.parent / f".perfmon-cache-{blg_path.stem}"


def manifest_path_for(blg_path: Path) -> Path:
    return cache_dir_for(blg_path) / "manifest.json"


def write_manifest(blg_path: Path, dataframes: dict[str, str], producer: str = CACHE_PRODUCER_DEFAULT) -> CacheManifest:
    """Write a manifest.json next to the parquet cache files."""
    if producer not in CACHE_VALID_PRODUCERS:
        raise ValueError(
            f"Unknown producer {producer!r}. Valid: {sorted(CACHE_VALID_PRODUCERS)}"
        )
    stat = blg_path.stat()
    manifest = CacheManifest(
        schema_version=CACHE_SCHEMA_VERSION,
        producer=producer,
        created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        blg_path=str(blg_path),
        blg_mtime_ns=stat.st_mtime_ns,
        blg_size=stat.st_size,
        dataframes=dict(dataframes),
    )
    path = manifest_path_for(blg_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": manifest.schema_version,
                "producer": manifest.producer,
                "created_at": manifest.created_at,
                "blg_path": manifest.blg_path,
                "blg_mtime_ns": manifest.blg_mtime_ns,
                "blg_size": manifest.blg_size,
                "dataframes": manifest.dataframes,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return manifest


def read_manifest(blg_path: Path) -> CacheManifest | None:
    """Read manifest.json if present and valid; otherwise None."""
    path = manifest_path_for(blg_path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema_version") != CACHE_SCHEMA_VERSION:
        return None
    try:
        return CacheManifest(
            schema_version=int(data["schema_version"]),
            producer=str(data.get("producer", CACHE_PRODUCER_DEFAULT)),
            created_at=str(data.get("created_at", "")),
            blg_path=str(data.get("blg_path", "")),
            blg_mtime_ns=int(data.get("blg_mtime_ns", 0)),
            blg_size=int(data.get("blg_size", 0)),
            dataframes=dict(data.get("dataframes") or {}),
        )
    except (TypeError, ValueError, KeyError):
        return None


def cache_is_fresh(blg_path: Path, manifest: CacheManifest) -> bool:
    """True when manifest matches the current size + mtime of the .blg."""
    try:
        stat = blg_path.stat()
    except OSError:
        return False
    return stat.st_mtime_ns == manifest.blg_mtime_ns and stat.st_size == manifest.blg_size


def load_cached_dataframes(blg_path: Path, manifest: CacheManifest) -> dict[str, pd.DataFrame]:
    """Read parquet files referenced by the manifest."""
    out: dict[str, pd.DataFrame] = {}
    cache_dir = cache_dir_for(blg_path)
    for name, filename in manifest.dataframes.items():
        path = cache_dir / filename
        if not path.is_file():
            continue
        try:
            out[name] = pd.read_parquet(path)
        except Exception:  # noqa: BLE001 — stale parquet should fall through to re-export
            continue
    return out


def write_dataframes_to_cache(blg_path: Path, frames: dict[str, pd.DataFrame]) -> dict[str, str]:
    """Persist DataFrames as parquet. Returns the {name: filename} map for the manifest."""
    cache_dir = cache_dir_for(blg_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    for name, df in frames.items():
        filename = f"{name}.parquet"
        path = cache_dir / filename
        df.to_parquet(path, index=False)
        out[name] = filename
    return out


def _run_relog(blg_path: Path, csv_path: Path) -> tuple[bool, str]:
    """Spawn relog.exe and convert .blg -> .csv.

    Returns ``(success, error_message)``. ``error_message`` is empty on
    success.
    """
    relog = shutil.which("relog") or shutil.which("relog.exe")
    if relog is None:
        return (False, "relog.exe not found on PATH")
    try:
        result = subprocess.run(
            [
                relog,
                str(blg_path),
                "-o",
                str(csv_path),
                "-f",
                "CSV",
                "-t",
                "1",
                "-y",
            ],
            capture_output=True,
            text=True,
            timeout=_RELOG_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return (False, f"relog.exe timed out after {_RELOG_TIMEOUT_S}s")
    except OSError as exc:
        return (False, f"Failed to spawn relog.exe: {exc}")
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        return (False, err)
    return (True, "")


def _detect_csv_encoding(csv_path: Path) -> str:
    """Sniff the first 4 bytes of ``csv_path`` for a BOM.

    Returns the encoding name to pass to ``pd.read_csv``. PerfMon GUI
    exports are UTF-16 LE BOM; ``relog.exe -f CSV`` emits ASCII (no
    BOM); some Office tools write UTF-8 BOM. Default to UTF-8 when no
    BOM is present.
    """
    try:
        with open(csv_path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return "utf-8"
    if head.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if head.startswith(b"\xfe\xff"):
        return "utf-16-be"
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    return "utf-8"


def parse_relog_csv(csv_path: Path) -> pd.DataFrame:
    """Read the wide CSV produced by relog -f CSV and reshape to long form.

    relog emits a header row where the first column is the localized
    PDH timestamp label and every other column is a full counter path
    ``\\\\HOST\\Object(Instance)\\Counter``. Cells contain numeric strings
    or empty strings (missing samples).

    Output columns: ``Timestamp``, ``FullPath``, ``Hostname``,
    ``Object``, ``Instance``, ``Counter``, ``Value``.

    Encoding: the file is BOM-sniffed first. PerfMon GUI exports are
    UTF-16 LE BOM, ``relog.exe -f CSV`` emits ASCII (no BOM), some
    tools write UTF-8 BOM. The pandas read uses the detected encoding
    so a UTF-16 PerfMon export does not silently parse to zero rows.

    Rows whose Value cannot be parsed as float are dropped (relog
    sometimes emits a blank or " " for the first sample of a counter).
    """
    columns = [
        "Timestamp",
        "FullPath",
        "Hostname",
        "Object",
        "Instance",
        "Counter",
        "Value",
    ]
    if not csv_path.is_file():
        return pd.DataFrame(columns=columns)

    encoding = _detect_csv_encoding(csv_path)
    try:
        wide = pd.read_csv(csv_path, dtype=str, keep_default_na=False, encoding=encoding)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, OSError, UnicodeError):
        return pd.DataFrame(columns=columns)
    if wide.empty or wide.shape[1] < 2:
        return pd.DataFrame(columns=columns)

    ts_col = wide.columns[0]
    counter_cols = list(wide.columns[1:])
    wide["__ts"] = pd.to_datetime(wide[ts_col], errors="coerce")

    long_rows: list[dict[str, Any]] = []
    for col in counter_cols:
        if not col or not col.startswith("\\\\"):
            continue
        host, obj, inst, counter = split_counter_path(col)
        values = pd.to_numeric(wide[col], errors="coerce")
        mask = values.notna()
        if not mask.any():
            continue
        ts = wide["__ts"][mask]
        v = values[mask]
        for timestamp, value in zip(ts.tolist(), v.tolist()):
            long_rows.append(
                {
                    "Timestamp": timestamp,
                    "FullPath": col,
                    "Hostname": host,
                    "Object": obj,
                    "Instance": inst,
                    "Counter": counter,
                    "Value": value,
                }
            )
    if not long_rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(long_rows, columns=columns)


def export_blg(blg_path: Path) -> tuple[pd.DataFrame, str]:
    """Run relog.exe on a .blg and return the parsed long-form counters DataFrame.

    On success the second element is ``""`` (empty string). On failure
    the DataFrame is empty and the second element is a friendly error
    message that the caller is expected to surface verbatim in tool
    output.
    """
    cache_dir = cache_dir_for(blg_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    csv_path = cache_dir / "counters.csv"
    ok, err = _run_relog(blg_path, csv_path)
    if not ok:
        return pd.DataFrame(), err
    df = parse_relog_csv(csv_path)
    return df, ""


def build_log_dataframes(blg_path: Path) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """End-to-end: relog + parse + summarize. Returns ({name: df}, errors)."""
    counters, err = export_blg(blg_path)
    errors: list[str] = []
    if err:
        errors.append(err)
    summary = summarize_counters(counters)
    return ({"counters": counters, "summary": summary}, errors)
