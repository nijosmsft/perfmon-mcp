"""Per-scenario metadata for the vendored perfmon counter-set profiles.

Single source of truth for:

- which scenarios exist (``PROFILES`` dict)
- the counter list (read from the bundled ``.cset`` file)
- which analysis tools each scenario feeds
- runtime overhead notes (the mellanox-percpu scenario has a critical
  ~28pp delivery cost warning that ``list_counter_profiles`` and
  ``get_counter_profile`` must surface)
- the loader (``load_cset_text``) that reads the on-wheel ``.cset`` file
  via ``importlib.resources``
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import resources


@dataclass(frozen=True)
class ProfileMeta:
    """Metadata describing one perfmon counter-set scenario.

    Attributes:
        scenario: Short ID used by the MCP tools (e.g. ``"system-overview"``).
        title: One-line human title used in tables.
        when_to_use: 1-2 sentence guidance for an LLM picking from
            ``list_counter_profiles``.
        counter_sets: Counter-set short names included (e.g. ``["Processor"]``).
        analysis_tools: Analysis MCP tool names that read the resulting
            .blg well (used in ``get_capture_instructions``).
        privilege: Privilege requirement summary.
        recommended_duration_s: Suggested capture duration default.
        est_overhead: Human-readable overhead estimate.
        overhead_notes: Optional long-form warning (e.g. the Mellanox
            per-CPU ~28pp warning). Surfaced verbatim by tool output.
        notes: Free-form notes / caveats.
        cset_filename: Name of the bundled ``.cset`` resource.
    """

    scenario: str
    title: str
    when_to_use: str
    counter_sets: list[str] = field(default_factory=list)
    analysis_tools: list[str] = field(default_factory=list)
    privilege: str = "admin (logman + relog require elevation)"
    recommended_duration_s: int = 30
    est_overhead: str = "low"
    overhead_notes: str = ""
    notes: str = ""
    cset_filename: str = ""


PROFILES: dict[str, ProfileMeta] = {
    "system-overview": ProfileMeta(
        scenario="system-overview",
        title="System overview (lowest overhead)",
        when_to_use=(
            "Pick this when you want a fast 'what is happening on this box' "
            "snapshot. Total CPU + available memory + disk activity + "
            "per-NIC throughput. No per-CPU or per-queue breakdown."
        ),
        counter_sets=["Processor", "Memory", "PhysicalDisk", "Network Interface"],
        analysis_tools=[
            "snapshot_counters",
            "get_counter_summary",
            "get_counter_timeline",
        ],
        recommended_duration_s=30,
        est_overhead="negligible (~1 MB/min, 4 instances)",
        notes=(
            "Cannot answer per-CPU or per-RSS-queue questions - use "
            "cpu-detailed or mellanox-rss for those."
        ),
        cset_filename="system-overview.cset",
    ),
    "cpu-detailed": ProfileMeta(
        scenario="cpu-detailed",
        title="Per-CPU detail with DPC + interrupt rates",
        when_to_use=(
            "Pick this when you suspect a single CPU is hot, or want to "
            "see DPC / interrupt distribution across CPUs. Per-CPU "
            "Processor Time + Privileged Time + Interrupts/sec + DPCs "
            "Queued/sec + DPC Rate."
        ),
        counter_sets=["Processor"],
        analysis_tools=[
            "snapshot_counters",
            "get_counter_summary",
            "get_counter_timeline",
            "get_per_queue_summary",
        ],
        recommended_duration_s=30,
        est_overhead="low (scales with logical CPU count)",
        notes=(
            "Instance shape: \\Processor(<N>)\\* with N in 0..NumCpu-1 "
            "plus _Total. On an 80-CPU box this is ~400 counter columns."
        ),
        cset_filename="cpu-detailed.cset",
    ),
    "mellanox-rss": ProfileMeta(
        scenario="mellanox-rss",
        title="Mellanox WinOF-2 RSS + datapath (lightweight, Total-only)",
        when_to_use=(
            "Pick this for NIC RSS health on Mellanox ConnectX-6 Dx with "
            "the NVIDIA WinOF-2 driver. Captures the three Mellanox "
            "WinOF-2 counter sets via wildcard, which yields Total-only "
            "columns and is safe to overlap with a perf run."
        ),
        counter_sets=[
            "Mellanox WinOF-2 Rss Counters",
            "Mellanox WinOF-2 Receive Datapath Counters",
            "Mellanox WinOF-2 Transmit Datapath Counters",
        ],
        analysis_tools=[
            "snapshot_counters",
            "get_counter_summary",
            "get_per_queue_summary",
            "compare_logs",
        ],
        recommended_duration_s=30,
        est_overhead="low (<1pp delivery cost when wildcard Total-only)",
        notes=(
            "Requires the NVIDIA WinOF-2 MSI driver (the inbox netmlx5 "
            "driver does not register the Mellanox-vendor counter sets). "
            "Use 'mellanox-percpu' for the heavyweight per-CPU / per-queue "
            "explicit enumeration variant."
        ),
        cset_filename="mellanox-rss.cset",
    ),
    "mellanox-percpu": ProfileMeta(
        scenario="mellanox-percpu",
        title="Mellanox per-CPU / per-RqNum / per-SqNum (HEAVY, diagnostic-only)",
        when_to_use=(
            "Pick this only for distribution diagnostics: which CPU is "
            "hottest, is the Toeplitz hash uneven, which RqNum is hot. "
            "Never use for perf baselines - the explicit per-instance "
            "enumeration imposes a measurable delivery cost."
        ),
        counter_sets=[
            "Mellanox WinOF-2 Rss Counters",
            "Mellanox WinOF-2 Receive Datapath Counters",
            "Mellanox WinOF-2 Transmit Datapath Counters",
        ],
        analysis_tools=[
            "get_per_queue_summary",
            "get_counter_summary",
            "compare_logs",
        ],
        recommended_duration_s=30,
        est_overhead="HIGH (~28pp delivery cost at 400K offered load)",
        overhead_notes=(
            "WARNING: Mellanox per-CPU collection imposes ~28pp delivery "
            "cost at 400K offered load when actively collected. From the "
            "2026-06-01 PM measurement: wildcard Total-only mode showed "
            "99.45% delivery with 0.26% NIC drops; the explicit per-CPU + "
            "per-RqNum + per-SqNum 394-column mode dropped delivery to "
            "71.3% with 12.9% NIC drops. Relative ratios across CPUs / "
            "queues ARE preserved (the cost is uniform), but absolute "
            "perf numbers from this run should NOT be compared to a "
            "non-instrumented baseline. Use 'mellanox-rss' for any run "
            "where delivery rate matters."
        ),
        notes=(
            "The .cset file uses wildcard counter paths; the capture-time "
            "logman command does the per-instance enumeration via the "
            "Adapter #2 filter on the source system."
        ),
        cset_filename="mellanox-percpu.cset",
    ),
}


def load_cset_text(scenario: str) -> str:
    """Return the raw ``.cset`` XML for ``scenario``.

    Reads via ``importlib.resources`` so the lookup works whether the
    package is installed from source, a wheel, or a zipapp.

    Raises:
        KeyError: if ``scenario`` is not a known scenario.
        FileNotFoundError: if the bundled resource is missing - a
            packaging error.
    """
    if scenario not in PROFILES:
        raise KeyError(
            f"Unknown scenario {scenario!r}. Valid: "
            + ", ".join(sorted(PROFILES.keys()))
        )
    meta = PROFILES[scenario]
    resource = resources.files("perfmon_mcp.profiles").joinpath(meta.cset_filename)
    return resource.read_text(encoding="utf-8")


_COUNTER_RE = re.compile(r"<Counter>\s*(?P<path>[^<]+?)\s*</Counter>", re.IGNORECASE)


def extract_counter_paths(cset_text: str) -> list[str]:
    """Pull ``<Counter>...</Counter>`` paths out of a .cset XML string.

    The .cset XML uses a hand-readable subset, not the full
    ``PerformanceCounterDataCollector`` schema; we only need the
    counter paths. See ASSUMPTIONS.md for why this is sufficient.
    """
    return [m.group("path").strip() for m in _COUNTER_RE.finditer(cset_text)]
