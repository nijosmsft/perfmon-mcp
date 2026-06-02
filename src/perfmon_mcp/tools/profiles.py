"""Profile discovery tools: ``list_counter_profiles`` and ``get_counter_profile``."""

from __future__ import annotations

import pandas as pd

from perfmon_mcp.app import mcp
from perfmon_mcp.formatting.markdown import format_table
from perfmon_mcp.profiles.metadata import (
    PROFILES,
    extract_counter_paths,
    load_cset_text,
)


@mcp.tool()
def list_counter_profiles() -> str:
    """List the bundled perfmon counter-set profiles.

    Returns a markdown table with one row per scenario showing the
    title, when-to-use guidance, recommended duration, and overhead
    estimate. Scenarios marked HEAVY (overhead_notes populated) have a
    measurable perf cost - the warning is surfaced inline.
    """
    rows = []
    for scenario in sorted(PROFILES):
        meta = PROFILES[scenario]
        rows.append(
            {
                "Scenario": scenario,
                "Title": meta.title,
                "When to use": meta.when_to_use,
                "Recommended duration": f"{meta.recommended_duration_s}s",
                "Overhead": meta.est_overhead,
            }
        )
    df = pd.DataFrame(rows)
    table = format_table(df)

    warnings = [
        f"\n**!! {scenario}**: {PROFILES[scenario].overhead_notes}"
        for scenario in sorted(PROFILES)
        if PROFILES[scenario].overhead_notes
    ]
    if warnings:
        table += "\n" + "\n".join(warnings)
    return table


@mcp.tool()
def get_counter_profile(scenario: str) -> str:
    """Return the metadata + counter list for one scenario.

    Args:
        scenario: One of the IDs from ``list_counter_profiles``.

    Returns markdown with title, when-to-use, counter sets, analysis-
    tool affinity, overhead, the verbatim overhead_notes (if any), and
    the full counter-path list extracted from the bundled .cset.
    """
    if scenario not in PROFILES:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown scenario {scenario!r}. Valid: {valid}")

    meta = PROFILES[scenario]
    cset_text = load_cset_text(scenario)
    counters = extract_counter_paths(cset_text)

    parts: list[str] = [
        f"# {meta.title}",
        "",
        f"**Scenario**: `{scenario}`",
        f"**When to use**: {meta.when_to_use}",
        f"**Privilege**: {meta.privilege}",
        f"**Recommended duration**: {meta.recommended_duration_s}s",
        f"**Estimated overhead**: {meta.est_overhead}",
    ]

    if meta.overhead_notes:
        parts += ["", "## !! Overhead warning", "", meta.overhead_notes]

    if meta.notes:
        parts += ["", "## Notes", "", meta.notes]

    parts += [
        "",
        "## Counter sets",
        "",
        *(f"- `{cs}`" for cs in meta.counter_sets),
        "",
        "## Counter paths",
        "",
        "```",
        *counters,
        "```",
        "",
        "## Analysis tools that read this well",
        "",
        *(f"- `{tool}`" for tool in meta.analysis_tools),
    ]

    return "\n".join(parts)
