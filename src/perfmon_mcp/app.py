"""FastMCP application instance for perfmon-mcp.

Every tool module imports ``mcp`` from here and decorates its functions
with ``@mcp.tool()`` at import time, so ``server.py`` only needs to
import the submodules in the right order.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "perfmon-mcp",
    instructions=(
        "Capture and analyze Windows performance counters (PDH). Workflow "
        "is one of: live snapshot, capture-to-blg, or load-and-analyze. "
        "Tools are transport-agnostic -- run live commands locally, or "
        "ask for the command string and dispatch it through any remote "
        "MCP / PowerShell remoting / SSH / human paste.\n"
        "\n"
        "DISCOVER: `list_counter_profiles` returns the four bundled "
        "scenarios (`system-overview`, `cpu-detailed`, `mellanox-rss`, "
        "`mellanox-percpu`); `get_counter_profile(scenario)` returns the "
        "counter list and overhead notes for one. The mellanox-percpu "
        "profile has a known ~28pp delivery cost at 400K offered load -- "
        "use it for diagnostic distribution analysis, never for perf "
        "baselines.\n"
        "\n"
        "SNAPSHOT (live PDH read, one sample): "
        "`snapshot_counters(scenario, target='local'|'remote')`. Local "
        "spawns Get-Counter and returns a markdown table. Remote returns "
        "the Get-Counter command in a fenced powershell block; feed the "
        "remote stdout back to `parse_counter_output(text, scenario)` to "
        "produce the same markdown shape.\n"
        "\n"
        "CAPTURE (long-running, emit-only): "
        "`get_capture_commands(scenario, output_path, duration_s)` "
        "returns paste-ready 3-step logman (create + start/sleep/stop + "
        "relog). `get_capture_instructions(scenario, target, "
        "output_path)` returns the full runbook including remote "
        "transfer-back examples for PowerShell remoting, LabLink, and "
        "manual scp. Capture tools never execute.\n"
        "\n"
        "ANALYZE (after a .blg exists locally): `load_blg(path)` returns "
        "a `log_id`; pass it to every analysis tool. `list_loaded_logs` "
        "for the registry. `get_counter_summary(log_id, top_n)` for "
        "per-counter mean/p95/max. `get_counter_timeline(log_id, "
        "counter, bucket_seconds, max_rows)` for a single counter over "
        "time. `get_per_queue_summary(log_id, queue_filter)` aggregates "
        "instance counters by RqNum / SqNum / Cpu / Queue suffix. "
        "`compare_logs(baseline_log_id, test_log_id, top_n)` returns an "
        "A/B delta table.\n"
        "\n"
        "FEDERATION (optional, off by default): "
        "`get_evidence_status` reports whether the evidence-store "
        "library is installed AND `PERFMON_MCP_EVIDENCE_PATH` is set. "
        "`get_entities(log_id, entity_type, filter, max_rows)` lists "
        "registered entities; returns a friendly message when "
        "federation is disabled."
    ),
)
