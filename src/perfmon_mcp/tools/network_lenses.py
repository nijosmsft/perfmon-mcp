"""Network-focused convenience lenses over the loaded PDH counter set.

These tools annotate-and-delegate to :func:`get_counter_summary` (or
:func:`get_per_queue_summary`) with curated NIC-relevant counter
filters so callers don't have to remember exact ``Network Adapter(...)``
path syntax. The underlying summary / per-queue table is unchanged;
this module only narrows the rows shown and prepends a NIC-specific
header.

Tools:

- :func:`get_counter_throughput` — narrow to ``Network Adapter`` traffic
  counters (bytes/sec, packets/sec, errors). Optional ``nic_filter``
  substring matches against the instance string (e.g. the NIC
  description shown by ``discover_nics``).
- :func:`get_rss_distribution` — narrow a per-queue aggregate to the
  13 Mellanox WinOF-2 RSS counter names from the
  ``analyze-mellanox-rss`` skill, with Hot/Idle peer-group flags
  surfaced. Use after loading a ``mellanox-percpu`` or ``mellanox-rss``
  capture.
"""

from __future__ import annotations

import re

from perfmon_mcp.app import mcp
from perfmon_mcp.formatting.markdown import format_table
from perfmon_mcp.log_state import require_log
from perfmon_mcp.parsing.aggregator import per_queue_aggregate
from perfmon_mcp.profiles.metadata import PROFILES
from perfmon_mcp.tools.analyze import get_counter_summary

_THROUGHPUT_TERMS: tuple[str, ...] = (
    "bytes total/sec",
    "bytes received/sec",
    "bytes sent/sec",
    "packets/sec",
    "packets received/sec",
    "packets sent/sec",
    "packets received errors",
    "packets outbound errors",
    "packets received discarded",
    "packets outbound discarded",
    "current bandwidth",
    "output queue length",
)

_THROUGHPUT_OBJECT_HINTS: tuple[str, ...] = (
    "network adapter",
    "network interface",
)


# The 13 Mellanox WinOF-2 RSS counter names from the analyze-mellanox-rss
# skill. Kept here (not in profiles/metadata.py) because the lens is the
# only consumer; the profile's ``priority_metrics`` list is the
# authoritative subset and is used preferentially when present.
_MELLANOX_RSS_COUNTER_NAMES: tuple[str, ...] = (
    "Packets processed in NDIS poll mode",
    "Rss IPv4 Only",
    "Rss IPv4/Udp",
    "Rss IPv4/Tcp",
    "Rss IPv6 Only",
    "Rss IPv6/Udp",
    "Rss IPv6/Tcp",
    "Encapsulated Rss IPv4",
    "Encapsulated Rss IPv4/Udp",
    "Encapsulated Rss IPv4/Tcp",
    "NonRss IPv4",
    "NonRss IPv6",
    "Interrupts on incorrect cpu",
    "DpcWatchDog Starvation",
)


def _row_is_throughput(counter: str) -> bool:
    haystack = counter.lower()
    if not any(obj in haystack for obj in _THROUGHPUT_OBJECT_HINTS):
        return False
    return any(term in haystack for term in _THROUGHPUT_TERMS)


def _row_matches_nic(counter: str, nic_filter: str) -> bool:
    if not nic_filter or nic_filter == "*":
        return True
    return nic_filter.lower() in counter.lower()


@mcp.tool()
def get_counter_throughput(
    log_id: str,
    nic_filter: str = "*",
    top_n: int = 50,
) -> str:
    """Return Network Adapter throughput counters for a loaded log.

    Annotate-and-delegate wrapper over :func:`get_counter_summary`:
    selects the rows whose normalized counter path matches a curated
    NIC throughput set (Bytes Total/sec, Packets/sec, error and
    discard counters, Current Bandwidth, Output Queue Length), narrows
    by ``nic_filter`` when given, then prepends a NIC-specific header.
    The underlying summary table (and its column ordering) is unchanged.

    Args:
        log_id: ID returned by ``load_log``.
        nic_filter: Case-insensitive substring matched against the
            counter path (which includes the NIC instance name). Pass
            the exact ``InterfaceDescription`` from
            ``discover_nics`` to scope to a single NIC, or ``"*"``
            (default) to include every adapter.
        top_n: Cap the returned rows. Default 50.

    Returns markdown, just like ``get_counter_summary``. If the loaded
    log has no Network Adapter counters, the message says so explicitly
    instead of returning an empty table.
    """
    log = require_log(log_id)
    summary = log.summary
    if summary is None or summary.empty:
        return f"*No counter summary available for `{log_id}`.*"
    if "Counter" not in summary.columns:
        return (
            f"*Counter summary for `{log_id}` is missing the Counter column; "
            "cannot filter for throughput counters.*"
        )

    mask = summary["Counter"].apply(_row_is_throughput) & summary["Counter"].apply(
        lambda c: _row_matches_nic(c, nic_filter)
    )
    matched = summary[mask]
    if matched.empty:
        scope = "" if nic_filter in ("", "*") else f" matching `{nic_filter}`"
        return (
            f"*No Network Adapter throughput counters{scope} were found in "
            f"`{log_id}`. The log probably wasn't captured with a NIC-aware "
            "profile (try the `network` profile next time).*"
        )

    counter_filter = "|".join(
        re.escape(name) for name in matched["Counter"].astype(str).tolist()
    )
    delegated = get_counter_summary(
        log_id=log_id,
        top_n=top_n,
        counter_filter=counter_filter,
    )
    header = (
        f"**NIC throughput for `{log_id}`** "
        f"(nic_filter=`{nic_filter}`, {len(matched)} curated counters)\n\n"
    )
    return header + delegated


def _resolve_rss_counter_names(scenario_hint: str = "") -> list[str]:
    """Pick the curated RSS counter shortlist.

    When ``scenario_hint`` names a profile that carries a non-empty
    ``priority_metrics`` list, that wins (the LLM can refine the
    shortlist by writing a custom profile). Otherwise falls back to
    the module-level 13-name list from the analyze-mellanox-rss skill.
    """
    if scenario_hint and scenario_hint in PROFILES:
        meta = PROFILES[scenario_hint]
        if meta.priority_metrics:
            return list(meta.priority_metrics)
    return list(_MELLANOX_RSS_COUNTER_NAMES)


@mcp.tool()
def get_rss_distribution(
    log_id: str,
    adapter_filter: str = "",
    scenario_hint: str = "",
) -> str:
    """Per-CPU / per-RqNum / per-SqNum RSS counter distribution.

    Annotate-and-delegate wrapper over :func:`per_queue_aggregate`
    that narrows to the 13 curated Mellanox WinOF-2 RSS counter names
    from the analyze-mellanox-rss skill (or, when ``scenario_hint``
    names a profile with a non-empty ``priority_metrics`` list, that
    shortlist). The output is split into three sections — Cpu_N
    rows, RqNum_N rows, SqNum_N rows — and each section carries the
    Hot/Idle peer-group flags from ``per_queue_aggregate`` so the LLM
    can see queue-imbalance at a glance.

    Args:
        log_id: ID returned by ``load_log``.
        adapter_filter: Optional case-insensitive substring matched
            against the counter Instance string (e.g.
            ``"Adapter #2"`` to scope to the data NIC when a host has
            multiple Mellanox adapters). Empty includes every adapter
            in the log.
        scenario_hint: Optional scenario name (``"mellanox-percpu"`` /
            ``"mellanox-rss"``). When set and the profile carries
            ``priority_metrics``, that shortlist replaces the default
            13-name curated set.

    The aggregate columns (Mean, Min, Max, P95, Delta, MaxMinRatio,
    Hot, Idle) come from :func:`per_queue_aggregate` unchanged.
    """
    log = require_log(log_id)
    counters = log.counters
    if counters is None or counters.empty:
        return f"*No counters loaded for `{log_id}`.*"

    agg = per_queue_aggregate(counters)
    if agg.empty:
        return (
            f"*No per-queue / per-CPU counters in `{log_id}`. Capture "
            "a `mellanox-percpu` or `mellanox-rss` profile, or any "
            "profile with `Cpu_N` / `RqNum_N` / `SqNum_N` instances.*"
        )

    target_names = _resolve_rss_counter_names(scenario_hint)
    # Case-insensitive containment match: the curated names are the
    # short "counter" string (e.g. "Rss IPv4/Udp"); the aggregate
    # column carries the same value.
    needle_set = {n.lower() for n in target_names}
    name_mask = agg["CounterName"].astype(str).str.lower().isin(needle_set)
    scoped = agg[name_mask]

    if adapter_filter:
        adapter_needle = adapter_filter.lower()
        scoped = scoped[
            scoped["Instance"].astype(str).str.lower().str.contains(
                adapter_needle, na=False
            )
        ]

    if scoped.empty:
        scope = (
            f" matching adapter `{adapter_filter}`" if adapter_filter else ""
        )
        hint = f" (using `{scenario_hint}` priority_metrics)" if scenario_hint else ""
        return (
            f"*No RSS counters{scope}{hint} found in `{log_id}`. The "
            "log probably wasn't captured with a Mellanox profile; "
            "check the loaded counter set via `log_info`.*"
        )

    sections: list[str] = []
    header_scope = (
        f", adapter_filter=`{adapter_filter}`" if adapter_filter else ""
    )
    sections.append(
        f"**RSS distribution for `{log_id}`** "
        f"({len(target_names)} curated counters{header_scope}; "
        f"{len(scoped)} matched (counter,kind,index) rows)"
    )
    sections.append("")

    for kind_label, kind_value in (
        ("Per-CPU (Cpu_N)", "cpu"),
        ("Per-RSS-queue receive (RqNum_N)", "rqnum"),
        ("Per-send-queue (SqNum_N)", "sqnum"),
        ("Other (Queue_N)", "queue"),
    ):
        slice_ = scoped[scoped["Kind"].str.lower() == kind_value]
        if slice_.empty:
            continue
        hot_count = int(slice_["Hot"].sum()) if "Hot" in slice_.columns else 0
        idle_count = int(slice_["Idle"].sum()) if "Idle" in slice_.columns else 0
        sections.append(
            f"## {kind_label} ({len(slice_)} rows; {hot_count} hot, "
            f"{idle_count} idle)"
        )
        sections.append("")
        sections.append(format_table(slice_, max_rows=200))
        sections.append("")

    if len(sections) <= 2:
        # Header lines only — slices were all empty (defensive; the
        # outer scoped.empty check should have caught this).
        return (
            f"*The {len(target_names)} curated RSS counters were "
            f"found in `{log_id}` but none had recognized per-CPU / "
            "per-queue instances.*"
        )
    return "\n".join(sections).rstrip() + "\n"
