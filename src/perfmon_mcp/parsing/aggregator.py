"""Counter-name normalization, time bucketing, percentile helpers.

These helpers are shared across all .blg analysis tools so the
per-counter / per-queue / compare paths produce consistent column
names and bucket semantics.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd


# Match instance suffixes like RqNum_5, SqNum_12, Queue_3, Cpu_27.
# Used by get_per_queue_summary to attribute counters to a "queue
# index" without hardcoding any vendor string.
_QUEUE_SUFFIX_RE = re.compile(
    r"""(?P<kind>RqNum|SqNum|Queue|Cpu)_(?P<idx>\d+)""",
    re.IGNORECASE | re.VERBOSE,
)


# Split a full PDH path into (Hostname, Object, Instance, Counter).
_PATH_RE = re.compile(
    r"^\\\\(?P<host>[^\\]+)\\(?P<obj>[^\\(]+)(?:\((?P<inst>[^)]*)\))?\\(?P<counter>.+)$"
)


def split_counter_path(full_path: str) -> tuple[str, str, str, str]:
    """Decompose ``\\\\host\\object[(instance)]\\counter`` into 4 parts.

    Returns ``("", "", "", full_path)`` when the path doesn't match the
    expected shape; the caller decides whether to keep or drop the row.
    """
    m = _PATH_RE.match(full_path.strip())
    if not m:
        return ("", "", "", full_path)
    return (
        m.group("host").upper(),
        m.group("obj").strip(),
        (m.group("inst") or "").strip(),
        m.group("counter").strip(),
    )


def normalize_counter_name(full_path: str) -> str:
    """Strip the ``\\\\host\\`` prefix so the same counter on different
    hosts collapses to one row in summary tables. The full path is
    preserved as a separate column for audit.
    """
    host, obj, inst, counter = split_counter_path(full_path)
    if not host:
        return full_path
    if inst:
        return f"\\{obj}({inst})\\{counter}"
    return f"\\{obj}\\{counter}"


def extract_queue_index(instance: str) -> tuple[str, int] | None:
    """Return ``(kind, index)`` for instances matching RqNum/SqNum/Queue/Cpu.

    Returns None when no recognizable suffix is present. Used by
    ``get_per_queue_summary`` to attribute per-instance counters to a
    queue / CPU index without hardcoding any vendor string.
    """
    m = _QUEUE_SUFFIX_RE.search(instance or "")
    if not m:
        return None
    try:
        return (m.group("kind").lower(), int(m.group("idx")))
    except ValueError:
        return None


def percentile(series: pd.Series, q: float) -> float:
    """Return the q-th percentile (0..100) of a numeric pandas Series.

    Uses numpy.nanpercentile to ignore NaNs. Returns NaN for an empty
    or all-NaN series.
    """
    arr = pd.to_numeric(series, errors="coerce").to_numpy()
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, q))


def summarize_counters(counters_df: pd.DataFrame) -> pd.DataFrame:
    """Build a per-counter summary DataFrame from the long-form counters table.

    Input columns expected: ``FullPath``, ``Hostname``, ``Object``,
    ``Instance``, ``Counter``, ``Value``, ``Timestamp``.

    Output columns: ``Counter`` (normalized name), ``Hostname``,
    ``Object``, ``Instance``, ``CounterName``, ``Samples``,
    ``Mean``, ``Min``, ``Max``, ``P50``, ``P95``, ``P99``,
    ``FullPath``.

    Sorted by Mean descending. Rows with all-NaN values are kept (so
    callers can see broken counters) but have NaN stats.
    """
    if counters_df is None or counters_df.empty:
        return pd.DataFrame(
            columns=[
                "Counter",
                "Hostname",
                "Object",
                "Instance",
                "CounterName",
                "Samples",
                "Mean",
                "Min",
                "Max",
                "P50",
                "P95",
                "P99",
                "FullPath",
            ]
        )

    rows: list[dict[str, object]] = []
    grouped = counters_df.groupby("FullPath", sort=False)
    for full_path, group in grouped:
        values = pd.to_numeric(group["Value"], errors="coerce")
        host, obj, inst, counter_name = split_counter_path(str(full_path))
        rows.append(
            {
                "Counter": normalize_counter_name(str(full_path)),
                "Hostname": host or (group["Hostname"].iloc[0] if "Hostname" in group else ""),
                "Object": obj or (group["Object"].iloc[0] if "Object" in group else ""),
                "Instance": inst,
                "CounterName": counter_name,
                "Samples": int(values.notna().sum()),
                "Mean": float(values.mean()) if values.notna().any() else float("nan"),
                "Min": float(values.min()) if values.notna().any() else float("nan"),
                "Max": float(values.max()) if values.notna().any() else float("nan"),
                "P50": percentile(values, 50),
                "P95": percentile(values, 95),
                "P99": percentile(values, 99),
                "FullPath": str(full_path),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Mean", ascending=False, na_position="last").reset_index(drop=True)
    return df


def bucket_timeline(
    counters_df: pd.DataFrame,
    counter_substring: str,
    bucket_seconds: float = 1.0,
) -> pd.DataFrame:
    """Return per-bucket aggregated values for one counter.

    Filters ``counters_df`` to rows whose normalized counter name or
    FullPath contains ``counter_substring`` (case-insensitive). Groups
    the rows by ``bucket_seconds`` and per-instance, and computes mean
    / min / max per bucket.

    Columns: ``BucketStart``, ``Counter``, ``Instance``, ``Mean``,
    ``Min``, ``Max``, ``Samples``.
    """
    if counters_df is None or counters_df.empty:
        return pd.DataFrame(
            columns=["BucketStart", "Counter", "Instance", "Mean", "Min", "Max", "Samples"]
        )
    needle = counter_substring.strip().lower()
    if not needle:
        return pd.DataFrame(
            columns=["BucketStart", "Counter", "Instance", "Mean", "Min", "Max", "Samples"]
        )

    df = counters_df.copy()
    df["NormalizedCounter"] = df["FullPath"].apply(lambda p: normalize_counter_name(str(p)))
    mask = (
        df["NormalizedCounter"].str.lower().str.contains(needle, na=False)
        | df["FullPath"].str.lower().str.contains(needle, na=False)
    )
    df = df[mask].copy()
    if df.empty:
        return pd.DataFrame(
            columns=["BucketStart", "Counter", "Instance", "Mean", "Min", "Max", "Samples"]
        )

    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    df = df.dropna(subset=["Timestamp"])
    if df.empty:
        return pd.DataFrame(
            columns=["BucketStart", "Counter", "Instance", "Mean", "Min", "Max", "Samples"]
        )
    bucket = pd.Timedelta(seconds=max(bucket_seconds, 0.001))
    df["BucketStart"] = df["Timestamp"].dt.floor(bucket)
    df["Value"] = pd.to_numeric(df["Value"], errors="coerce")

    grouped = df.groupby(["BucketStart", "NormalizedCounter", "Instance"], dropna=False)
    out = grouped.agg(
        Mean=("Value", "mean"),
        Min=("Value", "min"),
        Max=("Value", "max"),
        Samples=("Value", "count"),
    ).reset_index()
    out = out.rename(columns={"NormalizedCounter": "Counter"})
    out = out.sort_values(["BucketStart", "Counter", "Instance"]).reset_index(drop=True)
    return out


def per_queue_aggregate(
    counters_df: pd.DataFrame, queue_filter: str = ""
) -> pd.DataFrame:
    """Aggregate per-instance counters by queue/CPU index.

    For every counter whose instance string contains ``RqNum_N`` /
    ``SqNum_N`` / ``Queue_N`` / ``Cpu_N`` we extract the kind+index
    and emit one row per (CounterName, Kind, Index) with summary
    stats across all samples.

    Args:
        counters_df: Long-form counters DataFrame from a loaded log.
        queue_filter: Optional case-insensitive substring filter
            applied to the counter name; empty means no filter.

    Columns: ``CounterName``, ``Kind``, ``Index``, ``Instance``,
    ``Samples``, ``Mean``, ``Min``, ``Max``, ``P95``, ``Object``,
    ``Hostname``.
    """
    cols = [
        "CounterName",
        "Kind",
        "Index",
        "Instance",
        "Samples",
        "Mean",
        "Min",
        "Max",
        "P95",
        "Object",
        "Hostname",
    ]
    if counters_df is None or counters_df.empty:
        return pd.DataFrame(columns=cols)

    rows: list[dict[str, object]] = []
    needle = queue_filter.strip().lower()
    for full_path, group in counters_df.groupby("FullPath", sort=False):
        host, obj, inst, counter_name = split_counter_path(str(full_path))
        queue = extract_queue_index(inst)
        if queue is None:
            continue
        if needle and needle not in counter_name.lower():
            continue
        kind, idx = queue
        values = pd.to_numeric(group["Value"], errors="coerce")
        rows.append(
            {
                "CounterName": counter_name,
                "Kind": kind,
                "Index": idx,
                "Instance": inst,
                "Samples": int(values.notna().sum()),
                "Mean": float(values.mean()) if values.notna().any() else float("nan"),
                "Min": float(values.min()) if values.notna().any() else float("nan"),
                "Max": float(values.max()) if values.notna().any() else float("nan"),
                "P95": percentile(values, 95),
                "Object": obj,
                "Hostname": host,
            }
        )

    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df = df.sort_values(["CounterName", "Kind", "Index"]).reset_index(drop=True)
    return df


def compare_summaries(
    baseline: pd.DataFrame, test: pd.DataFrame, top_n: int = 50
) -> pd.DataFrame:
    """Build an A/B delta table from two per-counter summary DataFrames.

    Joins on ``Counter`` (the normalized path so the same logical
    counter on different hosts pairs up). Returns one row per counter
    present in either side with columns:

    - ``Counter``
    - ``BaselineMean`` (NaN when missing from baseline)
    - ``TestMean`` (NaN when missing from test)
    - ``Delta`` (test - baseline)
    - ``DeltaPct`` (delta / baseline * 100, NaN when baseline is 0 or NaN)
    - ``Status`` (baseline-only / test-only / both)

    Sorted by absolute Delta descending.
    """
    cols = ["Counter", "BaselineMean", "TestMean", "Delta", "DeltaPct", "Status"]
    if baseline is None:
        baseline = pd.DataFrame(columns=["Counter", "Mean"])
    if test is None:
        test = pd.DataFrame(columns=["Counter", "Mean"])

    baseline_slim = baseline[["Counter", "Mean"]].rename(columns={"Mean": "BaselineMean"})
    test_slim = test[["Counter", "Mean"]].rename(columns={"Mean": "TestMean"})
    merged = baseline_slim.merge(test_slim, on="Counter", how="outer")
    if merged.empty:
        return pd.DataFrame(columns=cols)

    def _status(row: pd.Series) -> str:
        b = pd.notna(row["BaselineMean"])
        t = pd.notna(row["TestMean"])
        if b and t:
            return "both"
        if b:
            return "baseline-only"
        return "test-only"

    merged["Delta"] = merged["TestMean"].astype(float) - merged["BaselineMean"].astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        merged["DeltaPct"] = np.where(
            (merged["BaselineMean"].abs() > 1e-9) & merged["BaselineMean"].notna(),
            merged["Delta"] / merged["BaselineMean"] * 100.0,
            np.nan,
        )
    merged["Status"] = merged.apply(_status, axis=1)
    merged["AbsDelta"] = merged["Delta"].abs()
    merged = merged.sort_values("AbsDelta", ascending=False, na_position="last").head(top_n)
    return merged[cols].reset_index(drop=True)
