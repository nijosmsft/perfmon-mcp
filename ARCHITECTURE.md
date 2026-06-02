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

`target="remote"` short-circuits the spawn and emits a fenced ```powershell
block with the same `Get-Counter` command line. The remote agent runs it
and feeds the raw stdout back into `parse_counter_output(text, scenario)`
for parsing.

## 2. Capture (emit-only)

```
get_capture_commands(scenario, output_path, duration_s)
  -> renders 3-step logman:
       logman create counter <name> -cf counters.txt -o <output_path>.blg ...
       logman start <name>; Start-Sleep -Seconds <dur>; logman stop <name>
       relog <output_path>.blg -o <output_path>.csv -f csv -t 1
```

Capture tools never execute. The LLM is responsible for dispatching them
through any transport. The `get_capture_instructions(target="remote")`
runbook includes transfer-back examples for PowerShell remoting, LabLink
(one example MCP transport), and manual scp.

## 3. .blg analysis (cached)

```
load_blg(path)
  -> compute log_id (sha256 of path|size|mtime_ns, prefix "log_")
  -> export_dir = <path>.parent / ".perfmon-cache-<stem>" / ""
  -> if manifest.json with valid schema + matching mtime exists: rehydrate
  -> else: shell out
       relog.exe <path> -o <export_dir>/counters.csv -f csv -t 1
     parse CSV with parsing/relog.py
     compute summary DataFrame via parsing/aggregator.py
     write counters.parquet + summary.parquet + manifest.json
  -> register LogData in log_state.py
  -> return markdown summary + log_id

get_counter_summary / timeline / per-queue / compare_logs
  -> require_log(log_id) -> reads cached DataFrames -> markdown
```

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
| `tools/profiles.py` | `list_counter_profiles`, `get_counter_profile`. |
| `tools/snapshot.py` | `snapshot_counters`, `parse_counter_output`. |
| `tools/capture.py` | `get_capture_commands`, `get_capture_instructions`. |
| `tools/analyze.py` | `load_blg`, `list_loaded_logs`, `get_counter_summary`, `get_counter_timeline`, `get_per_queue_summary`, `compare_logs`. |
| `tools/evidence.py` | `get_evidence_status`, `get_entities`. |
| `profiles/metadata.py` | `ProfileMeta` dataclass + `PROFILES` dict + `load_cset_text()`. |
| `profiles/*.cset` | Vendored XML counter-set files (4 to start). |
| `parsing/relog.py` | Shells out to `relog.exe`, returns parsed DataFrame. |
| `parsing/getcounter.py` | Parses `Get-Counter` text output into a DataFrame. |
| `parsing/aggregator.py` | Counter-name normalization, time bucketing, percentile helpers. |
| `formatting/markdown.py` | `format_table`, `format_pct`. |

## Remote-friendly contract

1. Every live-execute tool takes `target: str = "local"`. `"local"`
   executes; `"remote"` returns the command as a fenced ```powershell```
   block, no execution.
2. Every emit-style tool has a sibling parser
   (`parse_counter_output`) that converts raw remote stdout back to the
   same markdown shape.
3. Every file path is an explicit argument; nothing defaults to a
   local-only location for remote workflows.
4. `get_capture_instructions(scenario, target="remote", ...)` runbook
   names three transports: PowerShell remoting, LabLink (one example MCP
   transport), and manual scp / copy.

There are zero imports of any orchestration library in `src/`. There are
zero environment variables prefixed with any orchestration name.
