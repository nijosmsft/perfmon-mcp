"""Analyze tools - operate on a .blg / .csv loaded via ``load_log``.

Tool list (all take ``log_id`` as the first argument; the registry is
explicit so multiple logs can be analyzed concurrently):

- :func:`load_log` (new in v0.2; ``load_blg`` is preserved as an alias).
- :func:`load_csv` (new in v0.2 - skips relog for `.csv` inputs).
- :func:`list_loaded_logs`
- :func:`unload_log`
- :func:`log_info`
- :func:`list_blgs`
- :func:`analyze` - mega-tool composing the standard sections.
- :func:`get_counter_summary`
- :func:`get_counter_timeline`
- :func:`get_per_queue_summary`
- :func:`compare_logs` (now with ``mode='counter'|'per_queue'|'per_cpu'``).

``load_log`` first probes the side-by-side ``.perfmon-cache-<stem>/``
manifest; on a hit it rehydrates from parquet (sub-second). On a miss
it shells out to ``relog.exe`` (for .blg) or reads the CSV directly
(for .csv) and writes a fresh cache (schema v1, producer=relog).
"""

from __future__ import annotations

import warnings
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from perfmon_mcp.app import mcp
from perfmon_mcp.evidence_integration import safe_register_entities_from_log
from perfmon_mcp.formatting.markdown import format_table
from perfmon_mcp.log_state import (
    LogData,
    list_loaded_logs as _list_loaded_logs,
    make_log_id,
    register_log,
    require_log,
    unregister_log,
)
from perfmon_mcp.parsing.aggregator import (
    bucket_timeline,
    compare_summaries,
    per_queue_aggregate,
    summarize_counters,
)
from perfmon_mcp.parsing.relog import (
    CACHE_PRODUCER_DEFAULT,
    build_log_dataframes,
    cache_dir_for,
    cache_is_fresh,
    load_cached_dataframes,
    parse_relog_csv,
    read_manifest,
    write_dataframes_to_cache,
    write_manifest,
)


def _build_log_summary(log: LogData, source: str, errors: list[str]) -> str:
    counters = log.counters
    summary = log.summary
    parts = [
        f"**load_log: `{log.blg_path}`**",
        "",
        f"- `log_id`: `{log.log_id}`",
        f"- Source: {source}",
        f"- Producer: `{log.producer}`",
        f"- Cache dir: `{log.export_dir}`",
        f"- Hosts: {', '.join(log.hostnames) or '-'}",
        f"- Distinct counters: {log.counter_count or 0}",
        f"- Samples (rows): {log.sample_count or 0}",
        f"- Duration: {log.duration_seconds or 0:.2f}s",
    ]
    if errors:
        parts.append("")
        parts.append("**Warnings**")
        for e in errors:
            parts.append(f"- {e}")
    parts.append("")
    parts.append(
        "Use `analyze(log_id)` for the standard composite report, or drill "
        "in with `get_counter_summary`, `get_counter_timeline`, "
        "`get_per_queue_summary`. Compare against another log with "
        "`compare_logs(..., mode='counter'|'per_queue'|'per_cpu')`."
    )
    if counters is not None and not counters.empty and summary is not None and not summary.empty:
        parts.append("")
        parts.append("**Top 10 counters by mean**")
        parts.append("")
        cols = ["Counter", "Samples", "Mean", "P95", "Max"]
        parts.append(format_table(summary.head(10)[cols], max_rows=10))
    return "\n".join(parts)


def _materialize_log_data(log: LogData) -> None:
    """Populate scalar metadata (samples/counters/duration/hosts) on the LogData."""
    counters = log.counters
    if counters is None or counters.empty:
        log.sample_count = 0
        log.counter_count = 0
        log.duration_seconds = 0.0
        log.hostnames = []
        return
    log.sample_count = int(len(counters))
    log.counter_count = int(counters["FullPath"].nunique()) if "FullPath" in counters else 0
    if "Timestamp" in counters:
        ts = pd.to_datetime(counters["Timestamp"], errors="coerce").dropna()
        if not ts.empty:
            log.duration_seconds = float((ts.max() - ts.min()).total_seconds())
    if "Hostname" in counters:
        log.hostnames = sorted({h for h in counters["Hostname"].dropna().tolist() if h})


def _load_via_relog(path: Path, force: bool) -> tuple[LogData, str, list[str]]:
    """Shared load implementation. Returns (log, source_label, errors)."""
    log_id = make_log_id(path)
    cache_dir = cache_dir_for(path)

    source = "rebuild"
    errors: list[str] = []
    dataframes: dict[str, pd.DataFrame] = {}
    producer = CACHE_PRODUCER_DEFAULT

    if not force:
        manifest = read_manifest(path)
        if manifest is not None and cache_is_fresh(path, manifest):
            cached = load_cached_dataframes(path, manifest)
            if "counters" in cached and "summary" in cached:
                dataframes = cached
                producer = manifest.producer
                source = f"cache ({manifest.created_at})"

    if not dataframes:
        frames, build_errors = build_log_dataframes(path)
        errors.extend(build_errors)
        dataframes = frames
        if dataframes.get("counters") is not None and not dataframes["counters"].empty:
            files = write_dataframes_to_cache(path, dataframes)
            write_manifest(path, files, producer=CACHE_PRODUCER_DEFAULT)
            producer = CACHE_PRODUCER_DEFAULT
            source = "rebuild (cached for next load)"

    log = LogData(
        log_id=log_id,
        blg_path=path,
        export_dir=cache_dir,
        producer=producer,
        dataframes=dataframes,
        export_errors=errors,
    )
    _materialize_log_data(log)
    return log, source, errors


def _load_via_csv(path: Path, force: bool) -> tuple[LogData, str, list[str]]:
    """Load a relog-style CSV directly (skip relog.exe shellout)."""
    log_id = make_log_id(path)
    cache_dir = cache_dir_for(path)

    source = "rebuild"
    errors: list[str] = []
    dataframes: dict[str, pd.DataFrame] = {}
    producer = CACHE_PRODUCER_DEFAULT

    if not force:
        manifest = read_manifest(path)
        if manifest is not None and cache_is_fresh(path, manifest):
            cached = load_cached_dataframes(path, manifest)
            if "counters" in cached and "summary" in cached:
                dataframes = cached
                producer = manifest.producer
                source = f"cache ({manifest.created_at})"

    if not dataframes:
        counters = parse_relog_csv(path)
        if counters.empty:
            errors.append(
                f"CSV `{path}` parsed to zero rows. Expected a wide "
                "relog-style CSV (header row of full counter paths)."
            )
        summary = summarize_counters(counters)
        dataframes = {"counters": counters, "summary": summary}
        if not counters.empty:
            files = write_dataframes_to_cache(path, dataframes)
            write_manifest(path, files, producer=CACHE_PRODUCER_DEFAULT)
            producer = CACHE_PRODUCER_DEFAULT
            source = "rebuild (cached for next load)"

    log = LogData(
        log_id=log_id,
        blg_path=path,
        export_dir=cache_dir,
        producer=producer,
        dataframes=dataframes,
        export_errors=errors,
    )
    _materialize_log_data(log)
    return log, source, errors


# ---------------------------------------------------------------------------
# Load / registry tools
# ---------------------------------------------------------------------------


@mcp.tool()
def load_log(path: str, force: bool = False) -> str:
    """Load a Windows perfmon log (.blg or .csv) and register it for analysis.

    For ``.blg`` files: shells out to ``relog.exe -f CSV -t 1``, parses
    the wide CSV into a long-form DataFrame, computes a per-counter
    summary, and persists both as parquet under
    ``<log-dir>/.perfmon-cache-<stem>/`` along with a v1 manifest.

    For ``.csv`` files: skips relog (CSV is already in the expected
    wide format) and goes straight to parse + summarize + cache.

    On a subsequent load: rehydrates from the parquet cache when the
    log file's size + mtime match the manifest. ``force=True`` bypasses
    the cache.

    Args:
        path: Absolute or relative path to a .blg or .csv file.
        force: When True, ignore any cached parquet and re-parse.

    Returns:
        Markdown summary: ``log_id``, hosts, counter / sample counts,
        cache source, and a Top-10 by mean table.
    """
    log_path = Path(path).expanduser().resolve()
    if not log_path.is_file():
        return f"File not found: `{log_path}`."

    suffix = log_path.suffix.lower()
    if suffix == ".csv":
        log, source, errors = _load_via_csv(log_path, force)
    else:
        log, source, errors = _load_via_relog(log_path, force)

    register_log(log)
    safe_register_entities_from_log(log)
    return _build_log_summary(log, source, errors)


@mcp.tool()
def load_csv(path: str, force: bool = False) -> str:
    """Load a relog-style wide CSV directly. Equivalent to ``load_log`` on a .csv.

    Use when the perfmon log was already converted to CSV (the relog
    step in the capture runbook writes one beside every .blg) and you
    want to skip the relog.exe shellout entirely. Useful for sharing
    captures via portable CSV instead of binary .blg.

    Args:
        path: Absolute or relative path to a relog-style wide CSV.
            First column is the PDH timestamp, every other column is a
            full ``\\\\HOST\\Object(Instance)\\Counter`` path.
        force: When True, ignore any cached parquet and re-parse.
    """
    csv_path = Path(path).expanduser().resolve()
    if not csv_path.is_file():
        return f"File not found: `{csv_path}`."
    log, source, errors = _load_via_csv(csv_path, force)
    register_log(log)
    safe_register_entities_from_log(log)
    return _build_log_summary(log, source, errors)


@mcp.tool()
def load_blg(path: str, force: bool = False) -> str:
    """Deprecated alias for :func:`load_log`. Kept for v0.1 compatibility.

    Args:
        path: Path to a .blg or .csv file.
        force: When True, ignore any cached parquet and re-parse.
    """
    warnings.warn(
        "load_blg is deprecated since v0.2; use load_log instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return load_log(path=path, force=force)


@mcp.tool()
def list_loaded_logs() -> str:
    """Show every log currently registered with ``load_log`` in this session."""
    logs = _list_loaded_logs()
    if not logs:
        return "*No logs loaded. Call `load_log(path=...)` first.*"
    rows = [
        {
            "log_id": log.log_id,
            "path": str(log.blg_path),
            "Hosts": ", ".join(log.hostnames) or "-",
            "Counters": log.counter_count or 0,
            "Samples": log.sample_count or 0,
            "Duration (s)": round(log.duration_seconds or 0, 2),
            "Producer": log.producer,
        }
        for log in logs
    ]
    return format_table(pd.DataFrame(rows))


@mcp.tool()
def unload_log(log_id: str) -> str:
    """Remove a loaded log from the registry. The parquet cache on disk is kept.

    Args:
        log_id: ID returned by ``load_log``.
    """
    log = require_log(log_id)
    if unregister_log(log_id):
        return f"Unloaded log `{log_id}` (`{log.blg_path.name}`)."
    return f"Log `{log_id}` was not loaded."


@mcp.tool()
def log_info(log_id: str) -> str:
    """Show metadata about a single loaded log.

    Args:
        log_id: ID returned by ``load_log``.
    """
    log = require_log(log_id)
    parts = [
        f"**Log `{log.log_id}`**",
        "",
        f"- Path: `{log.blg_path}`",
        f"- Cache dir: `{log.export_dir}`",
        f"- Producer: `{log.producer}`",
        f"- Hosts: {', '.join(log.hostnames) or '-'}",
        f"- Distinct counters: {log.counter_count or 0}",
        f"- Samples (rows): {log.sample_count or 0}",
        f"- Duration: {log.duration_seconds or 0:.2f}s",
    ]
    if log.export_errors:
        parts.append("")
        parts.append("**Load warnings**")
        for e in log.export_errors:
            parts.append(f"- {e}")
    return "\n".join(parts)


@mcp.tool()
def list_blgs(directory: str = "C:\\perfmon", pattern: str = "*.blg") -> str:
    """List perfmon log files in a directory.

    Lists both ``*.blg`` and ``*.csv`` files by default (any pattern
    listed in ``pattern`` is unioned with ``*.csv`` if it doesn't
    already match). Sorted by mtime descending so the freshest capture
    is at the top.

    Args:
        directory: Directory to search. Default ``C:\\perfmon``.
        pattern: Glob for binary captures. Default ``*.blg``. The .csv
            siblings produced by the capture runbook are also surfaced.
    """
    log_dir = Path(directory).expanduser()
    if not log_dir.exists():
        return f"Directory not found: `{directory}`."

    files: list[Path] = []
    files.extend(log_dir.glob(pattern))
    # Always also surface the .csv siblings; ``load_log`` handles both.
    if pattern != "*.csv":
        files.extend(log_dir.glob("*.csv"))
    # Deduplicate by resolved path while preserving sort below.
    seen: set[Path] = set()
    unique: list[Path] = []
    for f in files:
        rf = f.resolve()
        if rf in seen:
            continue
        seen.add(rf)
        unique.append(f)

    if not unique:
        return f"*No `{pattern}` or `*.csv` files in `{directory}`.*"

    unique.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    rows: list[dict[str, object]] = []
    for f in unique:
        stat = f.stat()
        rows.append(
            {
                "Name": f.name,
                "Kind": f.suffix.lower().lstrip("."),
                "Size (MB)": round(stat.st_size / (1024 * 1024), 2),
                "Modified": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M UTC"),
                "Path": str(f),
            }
        )
    df = pd.DataFrame(rows)
    return (
        f"**Perfmon logs in `{directory}`** ({len(rows)} files)\n\n"
        + format_table(df, max_rows=max(len(rows) + 1, 50))
        + "\n\nLoad one with `load_log(path='<Path>')`."
    )


# ---------------------------------------------------------------------------
# Analysis tools
# ---------------------------------------------------------------------------


_DEFAULT_ANALYZE_SECTIONS = ("overview", "top_counters", "per_queue", "warnings")
_VALID_ANALYZE_SECTIONS = set(_DEFAULT_ANALYZE_SECTIONS)


@mcp.tool()
def analyze(log_id: str, sections: str = "") -> str:
    """Composite analysis report for a loaded log.

    Composes the standard sections in a single call so the LLM can ask
    "tell me about this log" without choreographing four separate
    tools. Each section degrades gracefully when its source data is
    missing.

    Args:
        log_id: ID returned by ``load_log``.
        sections: Optional comma-separated subset of
            ``overview,top_counters,per_queue,warnings``.
            Default ``""`` means all four in order.
    """
    log = require_log(log_id)
    requested = (
        tuple(s.strip() for s in sections.split(",") if s.strip())
        if sections
        else _DEFAULT_ANALYZE_SECTIONS
    )
    unknown = [s for s in requested if s not in _VALID_ANALYZE_SECTIONS]
    if unknown:
        return (
            f"Unknown sections: {', '.join(unknown)}. Valid: "
            f"{', '.join(sorted(_VALID_ANALYZE_SECTIONS))}."
        )

    parts: list[str] = [f"# Analyze: `{log.blg_path.name}` (`{log.log_id}`)", ""]

    if "overview" in requested:
        parts.append("## Overview")
        parts.append("")
        parts.append(f"- Path: `{log.blg_path}`")
        parts.append(f"- Producer: `{log.producer}`")
        parts.append(f"- Hosts: {', '.join(log.hostnames) or '-'}")
        parts.append(f"- Distinct counters: {log.counter_count or 0}")
        parts.append(f"- Samples (rows): {log.sample_count or 0}")
        parts.append(f"- Duration: {log.duration_seconds or 0:.2f}s")
        parts.append("")

    if "top_counters" in requested:
        parts.append("## Top counters by mean")
        parts.append("")
        summary = log.summary
        if summary is None or summary.empty:
            parts.append("*No counter summary available.*")
        else:
            cols = [
                c for c in ["Counter", "Samples", "Mean", "P50", "P95", "Max"]
                if c in summary.columns
            ]
            parts.append(format_table(summary.head(15)[cols], max_rows=20))
        parts.append("")

    if "per_queue" in requested:
        parts.append("## Per-queue / per-CPU rollup")
        parts.append("")
        counters = log.counters
        if counters is None or counters.empty:
            parts.append("*No counters loaded.*")
        else:
            agg = per_queue_aggregate(counters)
            if agg.empty:
                parts.append(
                    "*No per-queue / per-CPU counters present "
                    "(no `RqNum_N` / `SqNum_N` / `Queue_N` / `Cpu_N` "
                    "instances).*"
                )
            else:
                parts.append(
                    f"{len(agg)} (counter, kind, index) rows. "
                    "Top 20 by mean:"
                )
                parts.append("")
                top = agg.sort_values("Mean", ascending=False).head(20)
                parts.append(format_table(top, max_rows=25))
        parts.append("")

    if "warnings" in requested:
        parts.append("## Warnings")
        parts.append("")
        if log.export_errors:
            for e in log.export_errors:
                parts.append(f"- {e}")
        else:
            parts.append("*No load-time warnings.*")
        parts.append("")

    return "\n".join(parts)


@mcp.tool()
def get_counter_summary(log_id: str, top_n: int = 50) -> str:
    """Return the per-counter mean / min / max / p50 / p95 / p99 table.

    Args:
        log_id: ID from ``load_log``.
        top_n: Cap the table to the top N counters by mean. Default 50.
    """
    log = require_log(log_id)
    summary = log.summary
    if summary is None or summary.empty:
        return f"*No counter summary available for `{log_id}`.*"
    df = summary.head(max(top_n, 1))
    cols = ["Counter", "Samples", "Mean", "Min", "Max", "P50", "P95", "P99"]
    available = [c for c in cols if c in df.columns]
    return (
        f"**Counter summary for `{log_id}`** (top {len(df)} of {len(summary)})\n\n"
        + format_table(df[available], max_rows=top_n + 5)
    )


@mcp.tool()
def get_counter_timeline(
    log_id: str,
    counter: str,
    bucket_seconds: float = 1.0,
    max_rows: int = 60,
) -> str:
    """Return a time-bucketed timeline for a single counter.

    Args:
        log_id: ID from ``load_log``.
        counter: Case-insensitive substring matched against the
            normalized counter name (and the full path as a fallback).
            E.g. ``"% Processor Time"`` or ``"RqNum_5"``.
        bucket_seconds: Aggregation bucket size. Default 1.0.
        max_rows: Cap on rows returned. Default 60.
    """
    log = require_log(log_id)
    counters = log.counters
    if counters is None or counters.empty:
        return f"*No counters loaded for `{log_id}`.*"
    timeline = bucket_timeline(counters, counter, bucket_seconds=bucket_seconds)
    if timeline.empty:
        return (
            f"*No counter matched `{counter}` in `{log_id}`. "
            "Try `get_counter_summary` to see available counter names.*"
        )
    df = timeline.head(max(max_rows, 1))
    return (
        f"**Counter timeline `{counter}` in `{log_id}`** "
        f"(bucket={bucket_seconds}s, {len(df)} of {len(timeline)} buckets)\n\n"
        + format_table(df, max_rows=max_rows + 5)
    )


@mcp.tool()
def get_per_queue_summary(log_id: str, queue_filter: str = "") -> str:
    """Aggregate per-instance counters by queue / CPU index.

    Walks every counter whose instance string contains a recognized
    suffix (``RqNum_N``, ``SqNum_N``, ``Queue_N``, ``Cpu_N``) and emits
    one row per (counter, kind, index). Generic - works for Mellanox
    RqNum, generic NIC Queue, and per-CPU counters without hardcoding
    any vendor string.

    Args:
        log_id: ID from ``load_log``.
        queue_filter: Optional case-insensitive substring filter on the
            counter name (e.g. ``"Packets"``).
    """
    log = require_log(log_id)
    counters = log.counters
    if counters is None or counters.empty:
        return f"*No counters loaded for `{log_id}`.*"
    agg = per_queue_aggregate(counters, queue_filter=queue_filter)
    if agg.empty:
        filter_msg = (
            f" matching `{queue_filter}`" if queue_filter else ""
        )
        return (
            f"*No per-queue/per-CPU counters{filter_msg} in `{log_id}`. "
            "Did you capture a profile with `Cpu_N` / `RqNum_N` / "
            "`SqNum_N` / `Queue_N` instances?*"
        )
    return (
        f"**Per-queue / per-CPU summary for `{log_id}`** "
        f"({len(agg)} (counter,kind,index) rows)\n\n"
        + format_table(agg, max_rows=200)
    )


_VALID_COMPARE_MODES = ("counter", "per_queue", "per_cpu")


def _compare_per_queue(
    baseline_log: LogData,
    test_log: LogData,
    *,
    kind_filter: str,
    top_n: int,
) -> pd.DataFrame:
    """Build an A/B delta table joined on (CounterName, Kind, Index).

    Used by ``compare_logs(mode='per_queue'|'per_cpu')``. The
    ``kind_filter`` is "" for all kinds or e.g. "Cpu" for per-CPU only.
    """
    cols = [
        "CounterName",
        "Kind",
        "Index",
        "BaselineMean",
        "TestMean",
        "Delta",
        "DeltaPct",
        "Status",
    ]

    def _agg(log: LogData) -> pd.DataFrame:
        if log.counters is None or log.counters.empty:
            return pd.DataFrame(columns=["CounterName", "Kind", "Index", "Mean"])
        agg = per_queue_aggregate(log.counters)
        if agg.empty:
            return pd.DataFrame(columns=["CounterName", "Kind", "Index", "Mean"])
        if kind_filter:
            agg = agg[agg["Kind"].str.lower() == kind_filter.lower()]
        return agg[["CounterName", "Kind", "Index", "Mean"]]

    base = _agg(baseline_log).rename(columns={"Mean": "BaselineMean"})
    test = _agg(test_log).rename(columns={"Mean": "TestMean"})
    if base.empty and test.empty:
        return pd.DataFrame(columns=cols)

    merged = base.merge(test, on=["CounterName", "Kind", "Index"], how="outer")
    if merged.empty:
        return pd.DataFrame(columns=cols)

    import numpy as np

    merged["Delta"] = (
        merged["TestMean"].astype(float) - merged["BaselineMean"].astype(float)
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        merged["DeltaPct"] = np.where(
            (merged["BaselineMean"].abs() > 1e-9) & merged["BaselineMean"].notna(),
            merged["Delta"] / merged["BaselineMean"] * 100.0,
            np.nan,
        )

    def _status(row: pd.Series) -> str:
        b = pd.notna(row["BaselineMean"])
        t = pd.notna(row["TestMean"])
        if b and t:
            return "both"
        if b:
            return "baseline-only"
        return "test-only"

    merged["Status"] = merged.apply(_status, axis=1)
    merged["AbsDelta"] = merged["Delta"].abs()
    merged = merged.sort_values(
        "AbsDelta", ascending=False, na_position="last"
    ).head(top_n)
    return merged[cols].reset_index(drop=True)


@mcp.tool()
def compare_logs(
    baseline_log_id: str,
    test_log_id: str,
    top_n: int = 50,
    mode: str = "counter",
) -> str:
    """A/B compare two loaded logs.

    Args:
        baseline_log_id: ID of the baseline log.
        test_log_id: ID of the test log.
        top_n: Cap on rows in the delta table. Default 50.
        mode: ``"counter"`` (default) joins on the normalized counter
            name. ``"per_queue"`` joins per (counter, kind, index)
            across the per-instance rollup - surfaces shifts in queue
            distribution. ``"per_cpu"`` is ``per_queue`` filtered to
            ``Kind == 'Cpu'``.

    Returns a table sorted by absolute Delta descending. Counters
    present in only one side are labeled baseline-only / test-only.
    """
    if mode not in _VALID_COMPARE_MODES:
        return (
            f"Unknown mode `{mode}`. Valid: "
            f"{', '.join(_VALID_COMPARE_MODES)}."
        )

    baseline_log = require_log(baseline_log_id)
    test_log = require_log(test_log_id)

    if mode == "counter":
        baseline_summary = (
            baseline_log.summary
            if baseline_log.summary is not None
            else summarize_counters(baseline_log.counters or pd.DataFrame())
        )
        test_summary = (
            test_log.summary
            if test_log.summary is not None
            else summarize_counters(test_log.counters or pd.DataFrame())
        )
        delta = compare_summaries(baseline_summary, test_summary, top_n=top_n)
        if delta.empty:
            return (
                f"*No comparable counters between `{baseline_log_id}` and "
                f"`{test_log_id}`.*"
            )
        return (
            f"**compare_logs (mode=counter): `{baseline_log_id}` vs "
            f"`{test_log_id}`** (top {len(delta)} by |Delta|)\n\n"
            + format_table(delta, max_rows=top_n + 5)
        )

    kind_filter = "Cpu" if mode == "per_cpu" else ""
    delta = _compare_per_queue(
        baseline_log, test_log, kind_filter=kind_filter, top_n=top_n
    )
    if delta.empty:
        scope = (
            "per-CPU counters"
            if mode == "per_cpu"
            else "per-queue / per-CPU counters"
        )
        return (
            f"*No comparable {scope} between `{baseline_log_id}` and "
            f"`{test_log_id}`.*"
        )
    return (
        f"**compare_logs (mode={mode}): `{baseline_log_id}` vs "
        f"`{test_log_id}`** (top {len(delta)} by |Delta|)\n\n"
        + format_table(delta, max_rows=top_n + 5)
    )
