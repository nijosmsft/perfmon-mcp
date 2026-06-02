# Architecture

perfmon-mcp is a single FastMCP stdio server. Three lifecycles exist:

## 1. Live snapshot

```
snapshot_counters(scenario, target="local")
  -> resolve profile (profiles/metadata.py)
  -> spawn powershell.exe Get-Counter -Counter <paths> -SampleInterval 1 -MaxSamples 1
  -> parsing/getcounter.py converts the text block to a DataFrame
  -> formatting/markdown.format_table -> markdown
```

`target="remote"` short-circuits the spawn and emits the LabLink-first
runbook built by `tools/_remote.format_remote_block`: a markdown intro,
a ```powershell``` fence with the Get-Counter command, and a ```json```
sidecar fence (`parse_with`, `shell`, `expected_runtime_s`,
`timeout_s`). The dispatcher runs the command and feeds the raw stdout
back into `parse_counter_output(text, scenario)`.

## 1b. Discovery (live, emit-only by default)

```
discover_counter_sets(target, vendor_filter)   -> Get-Counter -ListSet *
discover_nics(target)                          -> Get-NetAdapter
```

Both wrap their PowerShell command in the same LabLink-first block, and
both have a `parse_*_output` sibling for converting remote stdout back
to markdown. Counter sets are vendor-tagged
(Mellanox / Intel / Broadcom / Microsoft / Other).

## 2. Capture (emit-only)

```
get_capture_commands(scenario, output_path, duration_s)
  -> renders 6-step logman:
       clean / counter-file / create / start+sleep+stop / relog / verify

get_capture_status(target)
  -> emit-only logman query <managed-collector> + LabLink-first JSON sidecar
  -> stdout fed to parse_capture_status_output(text) to render the table
```

Capture tools never execute. The LLM is responsible for dispatching them
through any transport. The `get_capture_instructions(target="remote")`
runbook lists LabLink (preferred), PowerShell remoting (fallback), and
manual scp (fallback) — in that order.

## 3. .blg / .csv analysis (cached)

```
load_log(path)               # canonical loader since v0.2
load_blg(path)               # deprecated alias of load_log
load_csv(path)               # skip relog for plain CSV inputs
  -> compute log_id (sha256 of path|size|mtime_ns, prefix "log_")
  -> export_dir = <path>.parent / ".perfmon-cache-<stem>" / ""
  -> if manifest.json with valid schema + matching mtime exists: rehydrate
  -> else (.blg): shell out
       relog.exe <path> -o <export_dir>/counters.csv -f csv -t 1
     parse CSV with parsing/relog.py
     compute summary DataFrame via parsing/aggregator.py
     write counters.parquet + summary.parquet + manifest.json
  -> register LogData in log_state.py
  -> return markdown summary + log_id

analyze(log_id, sections)                              # mega-tool
get_counter_summary / timeline / per-queue / throughput / compare_logs
log_info / unload_log / list_loaded_logs / list_blgs   # registry helpers
  -> require_log(log_id) -> reads cached DataFrames -> markdown
```

`compare_logs` takes `mode='counter'|'per_queue'|'per_cpu'`. Counter
mode preserves the v0.1 behavior; the other two join per-instance
aggregates by `(CounterName, Kind, Index)` so an A/B run comparing two
mellanox-percpu captures (per-CPU) or two mellanox-rss captures
(per-queue) is one tool call.

### Cache manifest schema (v1)

```json
{
  "schema_version": 1,
  "producer": "relog",
  "created_at": "2026-...",
  "blg_path": "C:\\perfmon\\out.blg",
  "blg_mtime_ns": 1234567890,
  "blg_size": 9876543,
  "dataframes": {
    "counters": "counters.parquet",
    "summary": "summary.parquet"
  }
}
```

`producer` is one of `relog | native | csharp-pdh`. The latter two are
reserved escape hatches: a future native-PDH or C# sidecar replaces the
`relog.exe` shellout while keeping the same on-disk parquet schema, so a
mixed-pipeline reload (an older cache + a newer producer build) just works.

## Module map

| Module | Purpose |
|---|---|
| `server.py` | Entry point. Imports each `tools.*` module to register `@mcp.tool()`s, then `mcp.run("stdio")`. |
| `app.py` | Single `FastMCP("perfmon-mcp")` instance + server instructions. |
| `log_state.py` | `LogData` dataclass + registry. `make_log_id`, `register/get/require_log`. |
| `evidence_integration.py` | Optional federation hook. `try/except ImportError` + `PERFMON_MCP_EVIDENCE_PATH` env var. |
| `tools/_remote.py` | Shared LabLink-first remote-block helper (`format_remote_block`). |
| `tools/profiles.py` | `list_counter_profiles`, `get_counter_profile`. |
| `tools/snapshot.py` | `snapshot_counters`, `parse_counter_output`, `discover_counter_sets`, `parse_counter_sets_output`, `discover_nics`, `parse_nics_output`. |
| `tools/capture.py` | `get_capture_commands`, `get_capture_instructions`, `get_capture_status`, `parse_capture_status_output`. |
| `tools/analyze.py` | `load_log`, `load_csv`, `load_blg` (deprecated alias), `list_loaded_logs`, `log_info`, `unload_log`, `list_blgs`, `analyze`, `get_counter_summary`, `get_counter_timeline`, `get_per_queue_summary`, `compare_logs`. |
| `tools/network_lenses.py` | `get_counter_throughput` (NIC convenience lens). |
| `tools/evidence.py` | `get_evidence_status`, `get_entities`. |
| `profiles/metadata.py` | `ProfileMeta` dataclass + `PROFILES` dict + `load_cset_text()`. |
| `profiles/*.cset` | Vendored XML counter-set files (4 to start). |
| `parsing/relog.py` | Shells out to `relog.exe`, returns parsed DataFrame. |
| `parsing/getcounter.py` | Parses `Get-Counter` text output into a DataFrame. |
| `parsing/aggregator.py` | Counter-name normalization, time bucketing, percentile helpers. |
| `formatting/markdown.py` | `format_table`, `format_pct`. |

## Remote-friendly contract

1. Every live-execute tool takes `target: str = "local"`. `"local"`
   executes; `"remote"` returns the LabLink-first runbook built by
   `tools/_remote.format_remote_block` (markdown intro +
   ```powershell``` fence with command + ```json``` sidecar fence
   with `parse_with`, `shell`, `expected_runtime_s`, `timeout_s`).
2. Every emit-style tool has a sibling parser
   (`parse_counter_output`, `parse_counter_sets_output`,
   `parse_nics_output`, `parse_capture_status_output`) that converts
   raw remote stdout back to the same markdown shape.
3. Every file path is an explicit argument; nothing defaults to a
   local-only location for remote workflows.
4. `get_capture_instructions(scenario, target="remote", ...)` runbook
   lists LabLink (preferred), PowerShell remoting (fallback), and
   manual scp (fallback) — in that order.

There are zero imports of any orchestration library in `src/`. There are
zero environment variables prefixed with any orchestration name. JSON
sidecars are restricted to primitive key/values (`str`, `int`, `float`,
`bool`, `None`) by contract.
