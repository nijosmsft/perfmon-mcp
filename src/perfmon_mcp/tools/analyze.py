"""Analyze tools - operate on a .blg loaded via ``load_blg``.

Tool list (all take ``log_id`` as the first argument; the registry is
explicit so multiple logs can be analyzed concurrently):

- :func:`load_blg`
- :func:`list_loaded_logs`
- :func:`get_counter_summary`
- :func:`get_counter_timeline`
- :func:`get_per_queue_summary`
- :func:`compare_logs`

``load_blg`` first probes the side-by-side ``.perfmon-cache-<stem>/``
manifest; on a hit it rehydrates from parquet (sub-second). On a miss
it shells out to ``relog.exe`` and writes a fresh cache (schema v1,
producer=relog).
"""

from __future__ import annotations

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
    read_manifest,
    write_dataframes_to_cache,
    write_manifest,
)


def _build_log_summary(log: LogData, source: str, errors: list[str]) -> str:
    counters = log.counters
    summary = log.summary
    parts = [
        f"**load_blg: `{log.blg_path}`**",
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
    parts.append("Use `get_counter_summary`, `get_counter_timeline`, "
                 "`get_per_queue_summary` to drill in. Compare against "
                 "another log with `compare_logs`.")
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


@mcp.tool()
def load_blg(path: str, force: bool = False) -> str:
    """Load a Windows perfmon .blg file and register it for analysis.

    On a fresh load: shells out to ``relog.exe -f CSV -t 1``, parses
    the wide CSV into a long-form DataFrame, computes a per-counter
    summary, and persists both as parquet under
    ``<blg-dir>/.perfmon-cache-<stem>/`` along with a v1 manifest.

    On a subsequent load: rehydrates from the parquet cache when the
    .blg's size + mtime match the manifest. ``force=True`` bypasses
    the cache.

    Args:
        path: Absolute or relative path to a .blg file.
        force: When True, ignore any cached parquet and re-run relog.

    Returns:
        Markdown summary: ``log_id``, hosts, counter / sample counts,
        cache source, and a Top-10 by mean table.
    """
    blg_path = Path(path).expanduser().resolve()
    if not blg_path.is_file():
        return f"File not found: `{blg_path}`."

    log_id = make_log_id(blg_path)
    cache_dir = cache_dir_for(blg_path)

    source = "rebuild"
    errors: list[str] = []
    dataframes: dict[str, pd.DataFrame] = {}
    producer = CACHE_PRODUCER_DEFAULT

    if not force:
        manifest = read_manifest(blg_path)
        if manifest is not None and cache_is_fresh(blg_path, manifest):
            cached = load_cached_dataframes(blg_path, manifest)
            if "counters" in cached and "summary" in cached:
                dataframes = cached
                producer = manifest.producer
                source = f"cache ({manifest.created_at})"

    if not dataframes:
        frames, build_errors = build_log_dataframes(blg_path)
        errors.extend(build_errors)
        dataframes = frames
        if dataframes.get("counters") is not None and not dataframes["counters"].empty:
            files = write_dataframes_to_cache(blg_path, dataframes)
            write_manifest(blg_path, files, producer=CACHE_PRODUCER_DEFAULT)
            producer = CACHE_PRODUCER_DEFAULT
            source = "rebuild (cached for next load)"

    log = LogData(
        log_id=log_id,
        blg_path=blg_path,
        export_dir=cache_dir,
        producer=producer,
        dataframes=dataframes,
        export_errors=errors,
    )
    _materialize_log_data(log)
    register_log(log)
    safe_register_entities_from_log(log)
    return _build_log_summary(log, source, errors)


@mcp.tool()
def list_loaded_logs() -> str:
    """Show every .blg currently registered with ``load_blg`` in this session."""
    logs = _list_loaded_logs()
    if not logs:
        return "*No .blg files loaded. Call `load_blg(path=...)` first.*"
    rows = [
        {
            "log_id": log.log_id,
            "blg_path": str(log.blg_path),
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
def get_counter_summary(log_id: str, top_n: int = 50) -> str:
    """Return the per-counter mean / min / max / p50 / p95 / p99 table.

    Args:
        log_id: ID from ``load_blg``.
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
        log_id: ID from ``load_blg``.
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
        log_id: ID from ``load_blg``.
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


@mcp.tool()
def compare_logs(baseline_log_id: str, test_log_id: str, top_n: int = 50) -> str:
    """A/B compare two loaded .blg logs by per-counter mean.

    Args:
        baseline_log_id: ID of the baseline log.
        test_log_id: ID of the test log.
        top_n: Cap on rows in the delta table. Default 50.

    Returns a table sorted by absolute Delta descending. Counters
    present in only one side are labeled baseline-only / test-only.
    """
    baseline_log = require_log(baseline_log_id)
    test_log = require_log(test_log_id)
    baseline_summary = baseline_log.summary if baseline_log.summary is not None else summarize_counters(baseline_log.counters or pd.DataFrame())
    test_summary = test_log.summary if test_log.summary is not None else summarize_counters(test_log.counters or pd.DataFrame())
    delta = compare_summaries(baseline_summary, test_summary, top_n=top_n)
    if delta.empty:
        return (
            f"*No comparable counters between `{baseline_log_id}` and "
            f"`{test_log_id}`.*"
        )
    return (
        f"**compare_logs: `{baseline_log_id}` vs `{test_log_id}`** "
        f"(top {len(delta)} by |Delta|)\n\n"
        + format_table(delta, max_rows=top_n + 5)
    )
