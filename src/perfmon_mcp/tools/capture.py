"""MCP tools for capturing perfmon .blg traces (emit-only, never executes).

The complement to ``load_blg``. These tools render paste-ready
``logman`` PowerShell that can run on the local machine, a LabLink MCP
target, an SSH session, or be handed to a human. They do NOT invoke
logman or relog themselves - the LLM workflow drives capture on
whatever transport makes sense, then calls ``load_blg`` against the
resulting .blg file.

Tool list:

- :func:`get_capture_commands` - paste-ready logman commands (create +
  start/sleep/stop + relog) for one scenario + output path + duration.
- :func:`get_capture_instructions` - long-form runbook including
  prerequisites, transfer-back examples (PowerShell remoting / LabLink
  / scp), and pointers at the analysis tools.

Both branch on ``meta.scenario == 'mellanox-percpu'`` to switch the
counter-list generation between (a) the .cset's wildcard counter paths
written verbatim to the logman -cf file, and (b) the explicit per-
instance enumeration filtered to 'Adapter #2' via Get-Counter
-ListSet. The latter is the heavy diagnostic mode whose ~28pp delivery
cost is surfaced in the profile metadata.
"""

from __future__ import annotations

import pandas as pd

from perfmon_mcp.app import mcp
from perfmon_mcp.formatting.markdown import format_table
from perfmon_mcp.profiles.metadata import (
    PROFILES,
    ProfileMeta,
    extract_counter_paths,
    load_cset_text,
)


_VALID_TARGETS = ("local", "remote")
_MIN_DURATION = 1
_MAX_DURATION = 3600

# Collector name used in all logman commands. Single fixed name keeps
# the teardown reliable - the runbook does an unconditional
# ``logman delete <name>`` at the end.
_COLLECTOR_NAME = "PerfmonMcpWatch"

# Sampling interval baked into the logman command. 1 second matches
# the mellanox-rss-metrics PowerShell skill and gives reasonable
# resolution for most perf work without ballooning the .blg.
_SAMPLE_INTERVAL = "00:00:01"

# Per-file size cap for bincirc (binary circular). 512 MB matches the
# mellanox skill default - large enough for a 30s default capture, small
# enough to refuse to fill the disk on a forgotten collector.
_MAX_FILE_MB = 512


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_scenarios_csv() -> str:
    return ", ".join(sorted(PROFILES.keys()))


def _scenario_or_error(scenario: str) -> ProfileMeta | str:
    """Return the ProfileMeta for ``scenario``, or a friendly error string."""
    meta = PROFILES.get(scenario)
    if meta is None:
        return (
            f"Unknown scenario `{scenario}`. Valid scenarios: "
            f"{_valid_scenarios_csv()}.\n\n"
            "Call `list_counter_profiles()` for an overview table."
        )
    return meta


def _target_or_error(target: str) -> str | None:
    """Return None when target is valid, else a friendly error string."""
    if target not in _VALID_TARGETS:
        return (
            f"Unknown target `{target}`. Valid: "
            f"{', '.join(_VALID_TARGETS)}. Use `local` for captures "
            "on the same machine you're calling from, or `remote` to "
            "get the transfer-back examples."
        )
    return None


def _validate_capture_args(
    scenario: str, output_path: str, duration_s: int
) -> ProfileMeta | str:
    """Common validation for the capture tools. Returns ProfileMeta on
    success or a friendly error string on failure."""
    meta_or_err = _scenario_or_error(scenario)
    if isinstance(meta_or_err, str):
        return meta_or_err
    if not output_path or not output_path.strip():
        return (
            "`output_path` must be a non-empty path (e.g. "
            "`C:\\perfmon\\capture.blg`).\n\n"
            "Call `list_counter_profiles()` to see the workflow."
        )
    if (
        not isinstance(duration_s, int)
        or duration_s < _MIN_DURATION
        or duration_s > _MAX_DURATION
    ):
        return (
            f"`duration_s` must be an integer between {_MIN_DURATION} "
            f"and {_MAX_DURATION} seconds (got {duration_s!r})."
        )
    return meta_or_err


def _csv_path_for(blg_path: str) -> str:
    """Derive a side-by-side .csv path for the relog output."""
    if blg_path.lower().endswith(".blg"):
        return blg_path[: -len(".blg")] + ".csv"
    return blg_path + ".csv"


def _counter_file_path_for(blg_path: str) -> str:
    """Derive a side-by-side counter-list .txt path for the -cf file."""
    if blg_path.lower().endswith(".blg"):
        base = blg_path[: -len(".blg")]
    else:
        base = blg_path
    return base + "_counters.txt"


def _metadata_table(meta: ProfileMeta) -> str:
    rows = [
        ("Title", meta.title),
        ("When to use", meta.when_to_use),
        ("Privilege", meta.privilege),
        ("Recommended duration", f"{meta.recommended_duration_s} s"),
        ("Estimated overhead", meta.est_overhead),
        (
            "Counter sets",
            ", ".join(meta.counter_sets) if meta.counter_sets else "-",
        ),
        (
            "Analysis tools",
            ", ".join(meta.analysis_tools) if meta.analysis_tools else "-",
        ),
        ("Bundled .cset", meta.cset_filename),
    ]
    if meta.notes:
        rows.append(("Notes", meta.notes))
    df = pd.DataFrame(rows, columns=["Field", "Value"])
    return format_table(df, max_rows=len(rows) + 1)


def _build_counter_file_step(meta: ProfileMeta, counter_file: str) -> str:
    """Render the PowerShell that materializes the -cf counter list file.

    For ``mellanox-percpu`` this enumerates Adapter #2 paths at runtime
    via Get-Counter -ListSet (the heavy diagnostic mode). For every
    other scenario it writes the wildcard counter paths from the
    bundled .cset verbatim.
    """
    if meta.scenario == "mellanox-percpu":
        return (
            "# Enumerate explicit per-instance Mellanox counter paths\n"
            "# (Adapter #2 = TEST-100G-2 data NIC). This is the HEAVY\n"
            "# variant - expect ~28pp delivery cost at 400K offered load.\n"
            "$rssPaths = (Get-Counter -ListSet 'Mellanox WinOF-2 Rss Counters').PathsWithInstances | Where-Object { $_ -match 'Adapter #2' }\n"
            "$rxPaths  = (Get-Counter -ListSet 'Mellanox WinOF-2 Receive Datapath Counters').PathsWithInstances | Where-Object { $_ -match 'Adapter #2' }\n"
            "$txPaths  = (Get-Counter -ListSet 'Mellanox WinOF-2 Transmit Datapath Counters').PathsWithInstances | Where-Object { $_ -match 'Adapter #2' }\n"
            f"($rssPaths + $rxPaths + $txPaths) | Set-Content '{counter_file}' -Encoding ASCII\n"
        )
    counters = extract_counter_paths(load_cset_text(meta.scenario))
    # Write a heredoc-style PowerShell array so the file is self-contained.
    array_literal = ",\n  ".join(f"'{c}'" for c in counters)
    return (
        "# Write the counter-list file. Wildcard paths (.cset verbatim).\n"
        "@(\n"
        f"  {array_literal}\n"
        f") | Set-Content '{counter_file}' -Encoding ASCII\n"
    )


def _build_logman_commands(
    meta: ProfileMeta, blg_path: str, duration_s: int
) -> str:
    """Render the full logman command block for a perfmon scenario."""
    counter_file = _counter_file_path_for(blg_path)
    csv_path = _csv_path_for(blg_path)
    counter_step = _build_counter_file_step(meta, counter_file)

    return (
        "```powershell\n"
        f"# Capture: scenario={meta.scenario}, blg={blg_path}, duration={duration_s}s\n"
        "\n"
        "# Step 0 - Clean any stale collector with the same name.\n"
        f"logman stop {_COLLECTOR_NAME} -ErrorAction SilentlyContinue 2>&1 | Out-Null\n"
        f"logman delete {_COLLECTOR_NAME} -ErrorAction SilentlyContinue 2>&1 | Out-Null\n"
        "\n"
        "# Step 1 - Materialize the counter-list file used by -cf.\n"
        f"{counter_step}"
        "\n"
        "# Step 2 - Create the collector. Binary circular (bincirc) file.\n"
        f"logman create counter {_COLLECTOR_NAME} -o '{blg_path}' -cf '{counter_file}' -si {_SAMPLE_INTERVAL} -f bincirc -max {_MAX_FILE_MB}\n"
        "\n"
        "# Step 3 - Start the collector, sleep for the requested duration, stop.\n"
        f"logman start {_COLLECTOR_NAME}\n"
        f"Start-Sleep -Seconds {duration_s}\n"
        f"logman stop {_COLLECTOR_NAME}\n"
        "\n"
        "# Step 4 - Convert .blg -> .csv for analysis.\n"
        f"relog '{blg_path}' -o '{csv_path}' -f CSV -y\n"
        "\n"
        "# Step 5 - MANDATORY teardown. Removes the collector definition and the counter list.\n"
        f"logman delete {_COLLECTOR_NAME} 2>&1 | Out-Null\n"
        f"Remove-Item '{counter_file}' -ErrorAction SilentlyContinue\n"
        "\n"
        "# Step 6 - Verify the .blg and .csv were written, and the collector is gone.\n"
        f"Get-Item '{blg_path}', '{csv_path}' -ErrorAction SilentlyContinue | Select-Object FullName, Length, LastWriteTime\n"
        f"logman query 2>&1 | Select-String -Pattern '{_COLLECTOR_NAME}'\n"
        "```\n"
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_capture_commands(
    scenario: str,
    output_path: str,
    duration_s: int = 30,
) -> str:
    """Return paste-ready logman commands to capture a perfmon .blg.

    Args:
        scenario: One of the IDs from ``list_counter_profiles``.
        output_path: Where the .blg should be written on the target,
            e.g. ``"C:\\perfmon\\capture.blg"``. The matching .csv is
            written next to it. Must be non-empty.
        duration_s: Capture window in seconds. Between 1 and 3600.
            Default 30.

    Returns:
        A markdown document with one ```powershell``` fenced block
        containing the 6-step logman command set (clean / counter-file /
        create / start+sleep+stop / relog / teardown / verify). For the
        ``mellanox-percpu`` scenario the counter-file step enumerates
        per-instance Adapter #2 paths via Get-Counter -ListSet; for
        every other scenario it writes the wildcard counter paths from
        the bundled .cset verbatim.

    This tool never executes anything. Run the commands locally, on a
    remote MCP node, on an SSH host, or by handing them to a human.
    """
    result = _validate_capture_args(scenario, output_path, duration_s)
    if isinstance(result, str):
        return result
    meta = result

    commands = _build_logman_commands(meta, output_path, duration_s)
    csv_path = _csv_path_for(output_path)

    warning = ""
    if meta.overhead_notes:
        warning = f"\n**!! Overhead warning**: {meta.overhead_notes}\n"

    return (
        f"**Capture commands** - scenario `{scenario}`, output "
        f"`{output_path}`, duration `{duration_s}s`\n"
        f"{warning}\n"
        f"{commands}\n"
        f"After the .blg is on your analysis machine, call "
        f"`load_blg(path='{output_path}')` to load it. "
        f"The matching CSV at `{csv_path}` is produced by the relog "
        "step and can be inspected directly.\n"
    )


@mcp.tool()
def get_capture_instructions(
    scenario: str,
    target: str = "local",
    output_path: str = "C:\\perfmon\\capture.blg",
) -> str:
    """Return a full step-by-step capture runbook for a perfmon scenario.

    Args:
        scenario: One of the IDs from ``list_counter_profiles``.
        target: ``"local"`` for captures on the same machine you are
            calling from, or ``"remote"`` to additionally get the
            transfer-back transport examples. Default ``"local"``.
        output_path: Where the .blg should be written on the target.
            Default ``"C:\\perfmon\\capture.blg"``.

    Returns:
        A long-form markdown runbook covering prerequisites, profile
        save, start/stop, verification, and (when ``target='remote'``)
        three transport examples for pulling the .blg back. Use this
        when you want narrative documentation; for just the paste-
        ready commands use ``get_capture_commands``.
    """
    target_err = _target_or_error(target)
    if target_err is not None:
        return target_err
    duration_s = (
        PROFILES[scenario].recommended_duration_s if scenario in PROFILES else 30
    )
    result = _validate_capture_args(scenario, output_path, duration_s)
    if isinstance(result, str):
        return result
    meta = result

    sections: list[str] = []

    # 1. Overview
    sections.append(f"# Capture runbook: `{scenario}` ({target} target)")
    sections.append("")
    sections.append("## 1. Overview")
    sections.append("")
    sections.append(
        f"This runbook captures a `{scenario}` perfmon log "
        f"({meta.title}) to `{output_path}` and prepares it for "
        f"`load_blg`. The capture uses logman + relog on the target "
        f"machine. Recommended duration is {meta.recommended_duration_s}s; "
        "adjust per workload."
    )
    sections.append("")
    sections.append(meta.when_to_use)
    sections.append("")

    # 2. Prerequisites
    sections.append("## 2. Prerequisites")
    sections.append("")
    sections.append(f"- Privilege: {meta.privilege}.")
    sections.append(
        "- `logman.exe` and `relog.exe` available on the target. "
        "Both ship in-box on Windows."
    )
    if meta.counter_sets:
        sections.append(
            "- Counter sets that must be registered on the target: "
            + ", ".join(f"`{cs}`" for cs in meta.counter_sets)
            + "."
        )
    if meta.scenario in {"mellanox-rss", "mellanox-percpu"}:
        sections.append(
            "- Mellanox WinOF-2 driver installed. The inbox `netmlx5` "
            "driver does NOT register the Mellanox-vendor counter "
            "sets; you need the NVIDIA WinOF-2 MSI."
        )
        sections.append(
            "- The data NIC under test should be identifiable as "
            "`Adapter #2` (the runbook filters on that string to "
            "avoid polluting collection with another Mellanox NIC's "
            "instances)."
        )
    if meta.notes:
        sections.append(f"- Notes: {meta.notes}")
    sections.append("")

    # 3. Warning (only for HEAVY scenarios)
    if meta.overhead_notes:
        sections.append("## 3. !! Overhead warning")
        sections.append("")
        sections.append(meta.overhead_notes)
        sections.append("")
        next_section = 4
    else:
        next_section = 3

    # N. Run the capture
    sections.append(f"## {next_section}. Run the capture")
    sections.append("")
    sections.append(
        "Run the 6-step block below. Equivalent to calling "
        f"`get_capture_commands('{scenario}', '{output_path}', "
        f"{meta.recommended_duration_s})`:"
    )
    sections.append("")
    sections.append(_build_logman_commands(meta, output_path, meta.recommended_duration_s))
    next_section += 1

    # N. Stop and verify
    sections.append(f"## {next_section}. Verify")
    sections.append("")
    sections.append(
        f"Step 6 of the block prints the .blg and .csv sizes plus the "
        f"output of `logman query` filtered to `{_COLLECTOR_NAME}` "
        "(empty == teardown succeeded). Sanity check: a 30s capture "
        "with the `system-overview` profile is typically 1-5 MB; "
        "`mellanox-percpu` with hundreds of per-instance columns can "
        "reach 50-200 MB."
    )
    sections.append("")
    next_section += 1

    # N. Transfer back (remote only)
    if target == "remote":
        sections.append(f"## {next_section}. Transfer the .blg back")
        sections.append("")
        sections.append(
            "Pull the `.blg` (and optionally the `.csv`) to the "
            "analysis machine. Three example transports - pick "
            "whichever matches your environment. LabLink is one "
            "example MCP transport; the same shape works with any "
            "MCP file-transfer tool."
        )
        sections.append("")
        csv_path = _csv_path_for(output_path)

        sections.append("**PowerShell remoting**")
        sections.append("")
        sections.append("```powershell")
        sections.append("$session = New-PSSession -ComputerName <host>")
        sections.append(
            f"Copy-Item -FromSession $session -Path '{output_path}' "
            "-Destination 'C:\\local\\perfmon\\capture.blg'"
        )
        sections.append(
            f"Copy-Item -FromSession $session -Path '{csv_path}' "
            "-Destination 'C:\\local\\perfmon\\capture.csv'"
        )
        sections.append("Remove-PSSession $session")
        sections.append("```")
        sections.append("")
        sections.append("**LabLink (or any equivalent MCP transport)**")
        sections.append("")
        sections.append("```text")
        sections.append(
            f"pull_file(node='<name>', remote_path='{output_path}', "
            "local_path='C:\\\\local\\\\perfmon\\\\capture.blg')"
        )
        sections.append(
            f"pull_file(node='<name>', remote_path='{csv_path}', "
            "local_path='C:\\\\local\\\\perfmon\\\\capture.csv')"
        )
        sections.append("```")
        sections.append("")
        sections.append("**Manual: scp / copy by hand**")
        sections.append("")
        sections.append("```bash")
        sections.append(
            f"scp <user>@<host>:'{output_path}' /local/perfmon/capture.blg"
        )
        sections.append(
            f"scp <user>@<host>:'{csv_path}' /local/perfmon/capture.csv"
        )
        sections.append("```")
        sections.append("")
        sections.append(
            "Or just have a human copy the file via RDP / file share "
            "/ USB key when the host is not reachable from automation."
        )
        sections.append("")
        next_section += 1

    # N. Load and analyze
    sections.append(f"## {next_section}. Load and analyze")
    sections.append("")
    sections.append(
        "Once the .blg is on the analysis machine, call "
        "`load_blg(path='<local_path>')` (where `<local_path>` is "
        f"`{output_path}` for `target='local'`, or wherever the "
        "transfer step wrote the file for `target='remote'`)."
    )
    sections.append("")
    if meta.analysis_tools:
        sections.append("Suggested analysis tools for this scenario:")
        sections.append("")
        for tool in meta.analysis_tools:
            sections.append(f"- `{tool}`")
        sections.append("")
    next_section += 1

    # N. Metadata recap
    sections.append(f"## {next_section}. Profile metadata recap")
    sections.append("")
    sections.append(_metadata_table(meta))
    sections.append("")

    return "\n".join(sections) + "\n"
