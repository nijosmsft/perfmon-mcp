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
  prerequisites, transfer-back examples (LabLink first, then
  PowerShell remoting, then scp), and pointers at the analysis tools.
- :func:`get_capture_status` - emit ``logman query`` runbook for the
  managed collector, with a JSON sidecar so any transport (LabLink
  first) can dispatch it and pipe the stdout to
  :func:`parse_capture_status_output`.
- :func:`parse_capture_status_output` - turn raw ``logman query`` stdout
  back into the same markdown table the local path would have rendered.

Both branch on ``meta.scenario == 'mellanox-percpu'`` to switch the
counter-list generation between (a) the .cset's wildcard counter paths
written verbatim to the logman -cf file, and (b) the explicit per-
instance enumeration filtered via the caller-supplied
``instance_filter`` (default = ``meta.default_instance_filter``, e.g.
``'Adapter #2'`` on the per-CPU Mellanox profile). The latter is the
heavy diagnostic mode whose ~28pp delivery cost is surfaced in the
profile metadata.
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
from perfmon_mcp.tools._remote import format_remote_block


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


def _build_counter_file_step(
    meta: ProfileMeta,
    counter_file: str,
    instance_filter: str = "",
) -> str:
    """Render the PowerShell that materializes the -cf counter list file.

    For ``mellanox-percpu`` (and any other scenario carrying a non-empty
    ``default_instance_filter``) this enumerates per-instance paths at
    runtime via Get-Counter -ListSet, filtered with PowerShell
    ``-match``. For every other scenario it writes the wildcard counter
    paths from the bundled .cset verbatim.

    The filter precedence is:
      1. ``instance_filter`` argument (caller's choice, wins).
      2. ``meta.default_instance_filter`` (profile default).
      3. ``""`` — enumerate every instance (almost always too many).
    """
    if meta.scenario == "mellanox-percpu":
        effective_filter = instance_filter or meta.default_instance_filter
        safe_filter = effective_filter.replace("'", "''") if effective_filter else ""
        filter_clause = (
            f" | Where-Object {{ $_ -match '{safe_filter}' }}"
            if safe_filter
            else ""
        )
        filter_comment = (
            f"# Per-instance filter: '{effective_filter}'.\n"
            if effective_filter
            else "# WARNING: no per-instance filter — every instance on every NIC will be captured.\n"
        )
        return (
            "# Enumerate explicit per-instance Mellanox counter paths.\n"
            f"{filter_comment}"
            "# This is the HEAVY variant - expect ~28pp delivery cost at 400K offered load.\n"
            "$rssPaths = (Get-Counter -ListSet 'Mellanox WinOF-2 Rss Counters').PathsWithInstances"
            f"{filter_clause}\n"
            "$rxPaths  = (Get-Counter -ListSet 'Mellanox WinOF-2 Receive Datapath Counters').PathsWithInstances"
            f"{filter_clause}\n"
            "$txPaths  = (Get-Counter -ListSet 'Mellanox WinOF-2 Transmit Datapath Counters').PathsWithInstances"
            f"{filter_clause}\n"
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
    meta: ProfileMeta,
    blg_path: str,
    duration_s: int,
    instance_filter: str = "",
) -> str:
    """Render the full logman command block for a perfmon scenario."""
    counter_file = _counter_file_path_for(blg_path)
    csv_path = _csv_path_for(blg_path)
    counter_step = _build_counter_file_step(meta, counter_file, instance_filter)

    return (
        "```powershell\n"
        f"# Capture: scenario={meta.scenario}, blg={blg_path}, duration={duration_s}s\n"
        "\n"
        "# Step 0 - Clean any stale collector with the same name.\n"
        "# Note: logman.exe is a native EXE, not a cmdlet - PowerShell common\n"
        "# parameters like -ErrorAction are NOT supported and produce\n"
        "# 'Argument is unknown' errors. Use '2>&1 | Out-Null' alone to\n"
        "# silently absorb 'no such collector' on a clean machine.\n"
        f"logman stop {_COLLECTOR_NAME} 2>&1 | Out-Null\n"
        f"logman delete {_COLLECTOR_NAME} 2>&1 | Out-Null\n"
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
    instance_filter: str = "",
) -> str:
    """Return paste-ready logman commands to capture a perfmon .blg.

    Args:
        scenario: One of the IDs from ``list_counter_profiles``.
        output_path: Where the .blg should be written on the target,
            e.g. ``"C:\\perfmon\\capture.blg"``. The matching .csv is
            written next to it. Must be non-empty.
        duration_s: Capture window in seconds. Between 1 and 3600.
            Default 30.
        instance_filter: Per-instance regex used by the per-instance
            enumeration step of the ``mellanox-percpu`` scenario.
            Empty defaults to the profile's
            ``default_instance_filter`` (``'Adapter #2'`` for
            ``mellanox-percpu``). Pass an explicit value to scope to a
            different NIC; pass ``"*"`` or any always-matching regex
            to enumerate every instance (rarely what you want).
            Ignored for scenarios whose counter file comes from the
            bundled .cset.

    Returns:
        A markdown document with one ```powershell``` fenced block
        containing the 6-step logman command set (clean / counter-file /
        create / start+sleep+stop / relog / teardown / verify). For the
        ``mellanox-percpu`` scenario the counter-file step enumerates
        per-instance paths via Get-Counter -ListSet, filtered by
        ``instance_filter``; for every other scenario it writes the
        wildcard counter paths from the bundled .cset verbatim.

    This tool never executes anything. Run the commands locally, on a
    remote MCP node, on an SSH host, or by handing them to a human.
    """
    result = _validate_capture_args(scenario, output_path, duration_s)
    if isinstance(result, str):
        return result
    meta = result

    commands = _build_logman_commands(meta, output_path, duration_s, instance_filter)
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
    instance_filter: str = "",
) -> str:
    """Return a full step-by-step capture runbook for a perfmon scenario.

    Args:
        scenario: One of the IDs from ``list_counter_profiles``.
        target: ``"local"`` for captures on the same machine you are
            calling from, or ``"remote"`` to additionally get the
            transfer-back transport examples. Default ``"local"``.
        output_path: Where the .blg should be written on the target.
            Default ``"C:\\perfmon\\capture.blg"``.
        instance_filter: Per-instance regex used by the per-instance
            enumeration step of the ``mellanox-percpu`` scenario.
            Empty defaults to the profile's
            ``default_instance_filter``. See
            ``get_capture_commands`` for the full description.

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
        effective_filter = instance_filter or meta.default_instance_filter
        if effective_filter:
            sections.append(
                "- The capture filters per-instance counter paths via "
                f"`-match '{effective_filter}'` (PowerShell regex). "
                "Confirm the data NIC under test is matched by that "
                "filter before running; use "
                "`discover_counter_instances(set_name=<...>, "
                f"instance_filter='{effective_filter}')` to preview the "
                "exact path list."
            )
        else:
            sections.append(
                "- No per-instance filter is set. The capture will "
                "enumerate **every** instance for every Mellanox "
                "counter set on the host. Pass `instance_filter` "
                "(e.g. `'Adapter #2'`) to scope to a single NIC."
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
        f"{meta.recommended_duration_s}, instance_filter='{instance_filter}')`:"
    )
    sections.append("")
    sections.append(
        _build_logman_commands(
            meta,
            output_path,
            meta.recommended_duration_s,
            instance_filter,
        )
    )
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
            "analysis machine. Three example transports - **prefer "
            "the LabLink (MCP) path** when available because it keeps "
            "the file transfer inside the same orchestration session "
            "that ran the capture; the PSRemoting and scp blocks are "
            "fallbacks for environments without a LabLink agent."
        )
        sections.append("")
        csv_path = _csv_path_for(output_path)

        sections.append("**LabLink (or any equivalent MCP transport) - preferred**")
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
        sections.append("**PowerShell remoting (fallback)**")
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
        sections.append("**Manual: scp / copy by hand (fallback)**")
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

# ---------------------------------------------------------------------------
# Capture status (logman query)
# ---------------------------------------------------------------------------


def _build_logman_query_command() -> str:
    """Render the ``logman query`` command for the managed collector.

    Always uses the fixed ``_COLLECTOR_NAME``, so a missing or stopped
    collector returns a non-zero exit and a recognizable
    ``Data Collector Set was not found`` stderr line. The parser treats
    both shapes (started + not-found) as valid outcomes.
    """
    return f"logman query '{_COLLECTOR_NAME}'"


def _parse_logman_query_text(text: str) -> dict[str, str]:
    """Parse ``logman query <name>`` stdout into a flat key/value dict.

    The logman output is ``Field: Value`` rows with a leading blank
    line. We split on the first colon and lowercase the keys for
    stable lookup. Lines that are not key/value pairs (banners, blank
    separators) are skipped. Recognizes the "not found" stderr shape
    and surfaces it as ``status=not_found``.
    """
    if not text or not text.strip():
        return {"status": "no_output", "raw": ""}

    lowered = text.lower()
    if "data collector set was not found" in lowered or "0x80300002" in lowered:
        return {"status": "not_found", "raw": text.strip()}

    parsed: dict[str, str] = {"raw": text.strip()}
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r\n").strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key_norm = key.strip().lower().replace(" ", "_")
        if not key_norm:
            continue
        parsed.setdefault(key_norm, value.strip())

    if "status" not in parsed:
        # Heuristic: if the canonical "Status:" row is present it wins;
        # otherwise infer "running" iff we found a Start Time row.
        if "start_time" in parsed and parsed["start_time"]:
            parsed["status"] = "running"
        else:
            parsed["status"] = "unknown"
    return parsed


def _format_capture_status(parsed: dict[str, str]) -> str:
    status = parsed.get("status", "unknown")
    header = f"**get_capture_status: `{_COLLECTOR_NAME}` -> `{status}`**"
    if status == "not_found":
        return (
            f"{header}\n\n"
            "No data collector by that name exists. Either the runbook "
            "was never started, or the teardown step already ran."
        )
    if status == "no_output":
        return f"{header}\n\n*logman produced no output.*"

    rows = [
        (k.replace("_", " ").title(), v)
        for k, v in parsed.items()
        if k not in {"raw", "status"} and v
    ]
    body = ""
    if rows:
        df = pd.DataFrame(rows, columns=["Field", "Value"])
        body = "\n\n" + format_table(df, max_rows=50)
    raw = parsed.get("raw", "")
    raw_block = ""
    if raw:
        raw_block = "\n\n<details><summary>Raw logman output</summary>\n\n```text\n" + raw[:4096] + "\n```\n</details>\n"
    return f"{header}{body}{raw_block}"


@mcp.tool()
def get_capture_status(target: str = "local") -> str:
    """Query the managed perfmon collector state via ``logman query``.

    Args:
        target: ``"local"`` returns a runbook hint plus the same command
            the remote path emits (the MCP itself never shells out to
            logman for state queries; this tool is emit-only). The
            output also includes the LabLink-first JSON sidecar so any
            transport can dispatch it and feed the stdout back to
            :func:`parse_capture_status_output`. Default ``"local"``.
    """
    target_err = _target_or_error(target)
    if target_err is not None:
        return target_err

    command = _build_logman_query_command()
    intro = (
        f"**get_capture_status (target={target})**\n\n"
        f"Queries the state of the managed collector `{_COLLECTOR_NAME}`. "
        "Run the command below, then feed the stdout back to "
        "`parse_capture_status_output(text=<stdout>)` to render the same "
        "table this tool would produce locally."
    )
    return format_remote_block(
        command,
        parse_with="parse_capture_status_output",
        expected_runtime_s=2,
        timeout_s=30,
        intro=intro,
    )


@mcp.tool()
def parse_capture_status_output(text: str) -> str:
    """Render the markdown status table from raw ``logman query`` stdout.

    Args:
        text: Raw stdout from the command emitted by
            :func:`get_capture_status`. Both the started shape
            (Status / Root Path / Segment / ... rows) and the
            "Data Collector Set was not found" shape are recognized.
    """
    if not text or not text.strip():
        return "*No text provided to parse.*"
    return _format_capture_status(_parse_logman_query_text(text))


# ---------------------------------------------------------------------------
# Teardown (force-clean the managed collector + any orphan perfmon processes)
# ---------------------------------------------------------------------------


def _build_teardown_command(collector_name: str) -> str:
    """Render the force-teardown command for one collector + perfmon procs.

    PowerShell verbs only — ``logman stop`` (best-effort, ignores errors
    if the collector isn't running), ``logman delete`` (best-effort
    likewise), then ``Stop-Process -Force`` on any straggler perfmon
    processes (perfmon.exe / typeperf.exe / relog.exe / logman.exe).
    The errors and Out-Null are deliberate — this is the "I don't care
    what state the box is in, just make sure nothing's collecting"
    cleanup.

    Important: ``logman.exe`` is a native executable, NOT a PowerShell
    cmdlet. It does not understand common parameters like
    ``-ErrorAction SilentlyContinue`` — passing them causes
    ``logman`` to emit ``Argument 'EA' is unknown`` /
    ``Argument 'SilentlyContinue' is unknown`` and exit with
    ``E_INVALIDARG`` (-2147024809). Because the trailing
    ``2>&1 | Out-Null`` swallows the message the failure looks silent
    and neither ``logman stop`` nor ``logman delete`` actually run.
    Use ``2>&1 | Out-Null`` alone to absorb the benign
    ``Data Collector Set was not found`` line on a clean box.

    Keep ``-ErrorAction SilentlyContinue`` on the
    ``Get-Process ... | Stop-Process`` pipeline — those ARE cmdlets
    and the parameter is meaningful (suppresses the noisy
    ``Cannot find a process with the name`` exceptions when nothing
    is running).
    """
    safe_name = collector_name.replace("'", "''")
    return (
        f"logman stop '{safe_name}' 2>&1 | Out-Null; "
        f"logman delete '{safe_name}' 2>&1 | Out-Null; "
        "Get-Process -Name perfmon, typeperf, relog, logman "
        "-ErrorAction SilentlyContinue | Stop-Process -Force "
        "-ErrorAction SilentlyContinue; "
        f"logman query 2>&1 | Select-String -Pattern '{safe_name}'"
    )


def _format_teardown(collector_name: str, text: str) -> str:
    """Render the teardown stdout as a friendly markdown block.

    The teardown command runs through several no-op-tolerant verbs and
    the final ``logman query | Select-String`` is the verification
    step: an empty result means the collector is gone. We treat ANY
    non-empty match as "still present" and call it out so the caller
    can rerun.
    """
    header = f"**get_teardown_commands: `{collector_name}`**"
    body = (text or "").strip()
    if not body:
        return (
            f"{header}\n\n"
            f"Teardown complete; `logman query | Select-String "
            f"'{collector_name}'` produced no output (collector is "
            "gone)."
        )
    return (
        f"{header}\n\n"
        f"Teardown ran. Verification output still mentions the "
        f"collector — rerun if needed:\n\n```text\n"
        f"{body[:4096]}\n```"
    )


@mcp.tool()
def get_teardown_commands(
    collector_name: str = _COLLECTOR_NAME,
    target: str = "local",
) -> str:
    """Emit a force-teardown runbook for a perfmon data collector.

    The MCP itself never shells out for this — both ``target='local'``
    and ``target='remote'`` return the same emit-only LabLink-first
    runbook + JSON sidecar, so the caller can dispatch the cleanup
    via any transport (LabLink for a lab node, PSRemoting, manual
    paste on the console).

    Use this when an earlier ``get_capture_commands`` run aborted
    mid-flight (the Step-5 teardown didn't execute), or when you
    suspect another orchestration session left a stale collector
    running. Safe to call repeatedly; every step is no-op-tolerant.

    Args:
        collector_name: Name of the collector to tear down. Default
            ``"PerfmonMcpWatch"`` matches the fixed name used by
            ``get_capture_commands``.
        target: ``"local"`` or ``"remote"`` — both emit the same
            block; the label only changes the intro text.
    """
    target_err = _target_or_error(target)
    if target_err is not None:
        return target_err
    if not collector_name or not collector_name.strip():
        return (
            "`collector_name` must be a non-empty string. Default is "
            f"`{_COLLECTOR_NAME}` (the name used by "
            "`get_capture_commands`)."
        )

    command = _build_teardown_command(collector_name)
    intro = (
        f"**get_teardown_commands (target={target})**\n\n"
        f"Force-cleans the managed collector `{collector_name}` plus "
        "any straggler perfmon / typeperf / relog / logman "
        "processes. Run the command below, then feed the stdout back "
        "to `parse_teardown_output(text=<stdout>, "
        f"collector_name='{collector_name}')` to render the "
        "verification block."
    )
    return format_remote_block(
        command,
        parse_with="parse_teardown_output",
        expected_runtime_s=5,
        timeout_s=30,
        intro=intro,
    )


@mcp.tool()
def parse_teardown_output(
    text: str,
    collector_name: str = _COLLECTOR_NAME,
) -> str:
    """Render the teardown verification block from raw remote stdout.

    Args:
        text: Raw stdout from the command emitted by
            :func:`get_teardown_commands`. The expected good output is
            empty (no rows match the collector name); any other text
            indicates the collector is still present.
        collector_name: Name to render in the header. Default
            ``"PerfmonMcpWatch"``.
    """
    if text is None:
        return "*No text provided to parse.*"
    return _format_teardown(collector_name, text)

