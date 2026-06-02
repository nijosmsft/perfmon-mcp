"""Network-focused convenience lenses over the loaded PDH counter set.

These tools annotate-and-delegate to :func:`get_counter_summary` with
curated NIC-relevant counter filters so callers don't have to remember
exact ``Network Adapter(...)`` path syntax. The underlying summary table
is unchanged; this module only narrows the rows shown.

Tools:

- :func:`get_counter_throughput` — narrow to ``Network Adapter`` traffic
  counters (bytes/sec, packets/sec, errors). Optional ``nic_filter``
  substring matches against the instance string (e.g. the NIC
  description shown by ``discover_nics``).
"""

from __future__ import annotations

import re

from perfmon_mcp.app import mcp
from perfmon_mcp.log_state import require_log
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
